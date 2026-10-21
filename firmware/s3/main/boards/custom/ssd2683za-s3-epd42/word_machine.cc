#include "word_machine.h"

#include <esp_random.h>

#include <cctype>
#include <cstdio>
#include <cstring>
#include <ctime>

#include "settings.h"

namespace {

std::string Trim(const std::string& s) {
    size_t begin = 0;
    size_t end = s.size();
    while (begin < end && std::isspace(static_cast<unsigned char>(s[begin]))) ++begin;
    while (end > begin && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;
    return s.substr(begin, end - begin);
}

// Seconds at local midnight for a "YYYY-MM-DD" string; 0 on parse failure.
time_t LocalMidnight(const std::string& date) {
    std::tm tm_value = {};
    if (date.size() != 10 || std::sscanf(date.c_str(), "%d-%d-%d",
                                         &tm_value.tm_year, &tm_value.tm_mon,
                                         &tm_value.tm_mday) != 3) {
        return 0;
    }
    tm_value.tm_year -= 1900;
    tm_value.tm_mon -= 1;
    tm_value.tm_hour = 12;  // noon avoids DST edge cases
    return std::mktime(&tm_value) - 12 * 3600;
}

// Extracts the Nth quoted string of a "[...]" item, or "" when missing.
std::string ExtractQuoted(const std::string& item, int index) {
    int found = 0;
    for (size_t i = 0; i < item.size(); ++i) {
        if (item[i] != '"') continue;
        const size_t start = ++i;
        while (i < item.size() && item[i] != '"') ++i;
        if (found++ == index) return item.substr(start, i - start);
    }
    return "";
}

// Extracts the Nth bare number of a "[...]" item, or fallback. Skips
// digits inside quoted strings (dates contain plenty of them).
int ExtractNumber(const std::string& item, int index, int fallback) {
    int found = 0;
    bool in_quote = false;
    size_t i = 0;
    while (i < item.size()) {
        const char c = item[i];
        if (c == '"') {
            in_quote = !in_quote;
            ++i;
        } else if (in_quote) {
            ++i;
        } else if (std::isdigit(static_cast<unsigned char>(c)) ||
                   (c == '-' && i + 1 < item.size() &&
                    std::isdigit(static_cast<unsigned char>(item[i + 1])))) {
            const size_t start = i;
            while (i < item.size() &&
                   (std::isdigit(static_cast<unsigned char>(item[i])) ||
                    item[i] == '-')) {
                ++i;
            }
            if (found++ == index) return std::atoi(item.substr(start).c_str());
        } else {
            ++i;
        }
    }
    return fallback;
}

// Splits a flat JSON array into the "[...]" item substrings.
std::vector<std::string> SplitJsonItems(const std::string& json) {
    std::vector<std::string> items;
    size_t depth = 0;
    size_t start = std::string::npos;
    for (size_t i = 0; i < json.size(); ++i) {
        const char c = json[i];
        if (c == '[') {
            if (++depth == 2) start = i;
        } else if (c == ']' && depth == 2) {
            items.push_back(json.substr(start, i - start + 1));
            depth = 1;
        } else if (c == ']' || c == '[') {
            --depth;
        }
    }
    return items;
}

}  // namespace

void WordMachine::SetWordListLoader(std::function<std::string()> loader) {
    word_list_loader_ = std::move(loader);
}

std::string WordMachine::TodayString() {
    const time_t now = std::time(nullptr);
    std::tm local = *std::localtime(&now);
    char buffer[40];
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d", local.tm_year + 1900,
                  local.tm_mon + 1, local.tm_mday);
    return std::string(buffer);
}

void WordMachine::EnsureLoaded() {
    if (loaded_) return;
    loaded_ = true;
    LoadHistory();
    LoadRecent();
    if (word_list_loader_) ParseWordList(word_list_loader_());
    if (entries_.empty()) return;
    const time_t day_number = std::time(nullptr) / 86400;
    today_index_ = static_cast<size_t>(day_number % entries_.size());
    cursor_ = today_index_;
}

void WordMachine::ParseWordList(const std::string& text) {
    entries_.clear();
    size_t start = 0;
    while (start <= text.size()) {
        size_t end = text.find('\n', start);
        std::string line = text.substr(
            start, end == std::string::npos ? std::string::npos : end - start);
        line = Trim(line);
        if (!line.empty() && line[0] != '#') {
            // Format: word,phonetic,pos,meaning[,example]
            std::string fields[5];
            size_t position = 0;
            int count = 0;
            while (count < 5) {
                const size_t comma = line.find(',', position);
                if (comma == std::string::npos || count == 4) {
                    fields[count++] = Trim(line.substr(position));
                    break;
                }
                fields[count++] = Trim(line.substr(position, comma - position));
                position = comma + 1;
            }
            if (!fields[0].empty() && !fields[3].empty()) {
                Entry entry;
                entry.word = fields[0];
                entry.phonetic = fields[1];
                entry.pos = fields[2];
                entry.meaning = fields[3];
                entry.example = fields[4];
                entries_.push_back(std::move(entry));
            }
        }
        if (end == std::string::npos) break;
        start = end + 1;
    }
}

void WordMachine::LoadHistory() {
    history_.clear();
    Settings settings("word", false);
    const std::string json = settings.GetString("history", "");
    for (const std::string& item : SplitJsonItems(json)) {
        DayRecord record;
        record.date = ExtractQuoted(item, 0);
        if (record.date.empty()) continue;
        record.correct = ExtractNumber(item, 1, 0);
        record.wrong = ExtractNumber(item, 2, 0);
        history_.push_back(record);
    }
}

void WordMachine::SaveHistory() const {
    std::string json = "[";
    for (size_t i = 0; i < history_.size(); ++i) {
        if (i) json += ",";
        json += "[\"" + history_[i].date + "\"," +
                std::to_string(history_[i].correct) + "," +
                std::to_string(history_[i].wrong) + "]";
    }
    json += "]";
    Settings settings("word", true);
    settings.SetString("history", json);
}

void WordMachine::LoadRecent() {
    recent_.clear();
    Settings settings("word", false);
    const std::string json = settings.GetString("recent", "");
    for (const std::string& item : SplitJsonItems(json)) {
        RecentWord recent;
        recent.word = ExtractQuoted(item, 0);
        recent.date = ExtractQuoted(item, 1);
        if (!recent.word.empty() && !recent.date.empty()) {
            recent_.push_back(std::move(recent));
        }
    }
}

void WordMachine::SaveRecent() const {
    std::string json = "[";
    for (size_t i = 0; i < recent_.size(); ++i) {
        if (i) json += ",";
        json += "[\"" + recent_[i].word + "\",\"" + recent_[i].date + "\"]";
    }
    json += "]";
    Settings settings("word", true);
    settings.SetString("recent", json);
}

void WordMachine::RecordSeen(const std::string& word) {
    // Keep the most recent words at the front; refresh the date of repeats.
    for (size_t i = 0; i < recent_.size(); ++i) {
        if (recent_[i].word == word) {
            if (recent_[i].date == TodayString() && i == 0) return;
            recent_[i].date = TodayString();
            RecentWord entry = recent_[i];
            recent_.erase(recent_.begin() + i);
            recent_.insert(recent_.begin(), entry);
            SaveRecent();
            return;
        }
    }
    RecentWord entry{word, TodayString()};
    recent_.insert(recent_.begin(), entry);
    if (recent_.size() > 30) recent_.resize(30);
    SaveRecent();
}

void WordMachine::RecordScore(bool correct) {
    const std::string today = TodayString();
    DayRecord* today_record = nullptr;
    for (auto& record : history_) {
        if (record.date == today) today_record = &record;
    }
    if (today_record == nullptr) {
        DayRecord record;
        record.date = today;
        history_.push_back(record);
        today_record = &history_.back();
    }
    if (correct) ++today_record->correct; else ++today_record->wrong;
    if (history_.size() > 120) history_.erase(history_.begin());
    SaveHistory();
    mode_ = Mode::kStats;
}

bool WordMachine::PickRecallWord() {
    const std::string today = TodayString();
    for (int attempt = 0; attempt < 8 && !recent_.empty(); ++attempt) {
        recall_index_ = esp_random() % recent_.size();
        const std::string& word = recent_[recall_index_].word;
        bool in_list = false;
        for (size_t i = 0; i < entries_.size(); ++i) {
            if (entries_[i].word == word) {
                in_list = true;
                break;
            }
        }
        if (!in_list) {
            recent_.erase(recent_.begin() + recall_index_);
            SaveRecent();
            continue;
        }
        const time_t seen = LocalMidnight(recent_[recall_index_].date);
        const time_t now = LocalMidnight(today);
        recall_days_ago_ =
            seen == 0 ? 0 : static_cast<int>((now - seen) / 86400);
        if (recall_days_ago_ < 0) recall_days_ago_ = 0;
        return true;
    }
    return false;
}

int WordMachine::Streak() const {
    if (history_.empty()) return 0;
    // Build a quick lookup of active days (any score that day).
    const std::string today = TodayString();
    const time_t today_midnight = LocalMidnight(today);
    if (today_midnight == 0) return 0;
    int streak = 0;
    bool today_active = false;
    for (const auto& record : history_) {
        if (record.date == today && record.correct + record.wrong > 0) {
            today_active = true;
            break;
        }
    }
    time_t day = today_active ? today_midnight : today_midnight - 86400;
    for (int guard = 0; guard < 120; ++guard, day -= 86400) {
        std::tm local = *std::localtime(&day);
        char buffer[40];
        std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d",
                      local.tm_year + 1900, local.tm_mon + 1, local.tm_mday);
        const std::string date(buffer);
        bool active = false;
        for (const auto& record : history_) {
            if (record.date == date && record.correct + record.wrong > 0) {
                active = true;
                break;
            }
        }
        if (!active) break;
        ++streak;
    }
    return streak;
}

void WordMachine::TodayTotals(int& correct, int& wrong) const {
    correct = 0;
    wrong = 0;
    const std::string today = TodayString();
    for (const auto& record : history_) {
        if (record.date == today) {
            correct = record.correct;
            wrong = record.wrong;
            return;
        }
    }
}

void WordMachine::Next() {
    switch (mode_) {
        case Mode::kLearn:
            if (entries_.empty()) return;
            cursor_ = (cursor_ + 1) % entries_.size();
            message_.clear();
            RecordSeen(entries_[cursor_].word);
            break;
        case Mode::kRecallQuestion:  // give up
            mode_ = Mode::kLearn;
            break;
        case Mode::kRecallAnswer:  // remembered
            RecordScore(true);
            break;
        case Mode::kStats:
            mode_ = Mode::kLearn;
            break;
    }
}

void WordMachine::Previous() {
    switch (mode_) {
        case Mode::kLearn:
            if (entries_.empty()) return;
            cursor_ = (cursor_ + entries_.size() - 1) % entries_.size();
            message_.clear();
            RecordSeen(entries_[cursor_].word);
            break;
        case Mode::kRecallQuestion:  // give up
            mode_ = Mode::kLearn;
            break;
        case Mode::kRecallAnswer:  // forgot
            RecordScore(false);
            break;
        case Mode::kStats:
            mode_ = Mode::kLearn;
            break;
    }
}

void WordMachine::Enter() {
    switch (mode_) {
        case Mode::kLearn: {
            if (entries_.empty()) return;
            if (PickRecallWord()) {
                mode_ = Mode::kRecallQuestion;
            } else {
                message_ = "先浏览学习几个单词，再来复习";
            }
            break;
        }
        case Mode::kRecallQuestion:
            mode_ = Mode::kRecallAnswer;
            break;
        case Mode::kRecallAnswer:  // skip without scoring
        case Mode::kStats:
            mode_ = Mode::kLearn;
            break;
    }
}

std::string WordMachine::Title() const {
    char buffer[64];
    switch (mode_) {
        case Mode::kLearn:
            std::snprintf(buffer, sizeof(buffer), "单词 %u/%u · 连续%d天",
                          static_cast<unsigned>(entries_.empty() ? 0 : cursor_ + 1),
                          static_cast<unsigned>(entries_.size()), Streak());
            return buffer;
        case Mode::kRecallQuestion:
            std::snprintf(buffer, sizeof(buffer), "回忆测试 · %d天前学过",
                          recall_days_ago_);
            return buffer;
        case Mode::kRecallAnswer:
            return "回忆测试 · 答案";
        case Mode::kStats:
            return "记忆统计";
    }
    return "单词";
}

std::string WordMachine::Body() const {
    if (entries_.empty()) {
        return "没有找到词表\n\n"
               "请在 TF 卡建目录并放入词表：\n"
               "/xiaozhi/words/words.txt\n\n"
               "每行一个单词：\n"
               "单词,音标,词性,释义,例句\n\n"
               "放入后重启设备加载";
    }
    std::string body;
    switch (mode_) {
        case Mode::kLearn: {
            const Entry& entry = entries_[cursor_];
            if (cursor_ == today_index_) body = "[今日] ";
            body += entry.word + "\n";
            if (!entry.phonetic.empty() || !entry.pos.empty()) {
                if (!entry.phonetic.empty()) body += entry.phonetic;
                if (!entry.pos.empty()) body += "  " + entry.pos;
                body += "\n";
            }
            body += "\n" + entry.meaning + "\n";
            if (!entry.example.empty()) {
                body += "\n例: " + entry.example + "\n";
            }
            if (!message_.empty()) body += "\n" + message_ + "\n";
            break;
        }
        case Mode::kRecallQuestion: {
            const RecentWord& recent = recent_[recall_index_];
            const Entry* entry = nullptr;
            for (const auto& candidate : entries_) {
                if (candidate.word == recent.word) {
                    entry = &candidate;
                    break;
                }
            }
            body = "还记得这个词的意思吗？\n\n";
            body += recent.word + "\n";
            if (entry && !entry->pos.empty()) body += entry->pos + "\n";
            body += "\n按 OK 看答案";
            break;
        }
        case Mode::kRecallAnswer: {
            const RecentWord& recent = recent_[recall_index_];
            const Entry* entry = nullptr;
            for (const auto& candidate : entries_) {
                if (candidate.word == recent.word) {
                    entry = &candidate;
                    break;
                }
            }
            if (entry != nullptr) {
                body = entry->word + "\n";
                if (!entry->phonetic.empty() || !entry->pos.empty()) {
                    if (!entry->phonetic.empty()) body += entry->phonetic;
                    if (!entry->pos.empty()) body += "  " + entry->pos;
                    body += "\n";
                }
                body += "\n" + entry->meaning + "\n";
                if (!entry->example.empty()) {
                    body += "\n例: " + entry->example + "\n";
                }
            } else {
                body = recent.word + "\n\n词条不在当前词表中";
            }
            body += "\n上键=记得  下键=忘了";
            break;
        }
        case Mode::kStats: {
            int total_correct = 0;
            int total_wrong = 0;
            for (const auto& record : history_) {
                total_correct += record.correct;
                total_wrong += record.wrong;
            }
            int today_correct = 0;
            int today_wrong = 0;
            TodayTotals(today_correct, today_wrong);
            char buffer[160];
            body = "今日  记得 " + std::to_string(today_correct) +
                   "   忘了 " + std::to_string(today_wrong) + "\n\n";
            std::snprintf(buffer, sizeof(buffer), "连续打卡  %d 天\n\n", Streak());
            body += buffer;
            if (total_correct + total_wrong > 0) {
                std::snprintf(buffer, sizeof(buffer), "累计  %d 测试 · 正确率 %d%%\n\n",
                              total_correct + total_wrong,
                              total_correct * 100 / (total_correct + total_wrong));
                body += buffer;
            } else {
                body += "累计  暂无测试记录\n\n";
            }
            body += "最近几天：\n";
            int shown = 0;
            for (auto it = history_.rbegin();
                 it != history_.rend() && shown < 4; ++it, ++shown) {
                std::snprintf(buffer, sizeof(buffer), "%s  ✓%d ✗%d\n",
                              it->date.c_str() + 5, it->correct, it->wrong);
                body += buffer;
            }
            if (shown == 0) body += "暂无记录";
            break;
        }
    }
    return body;
}

std::string WordMachine::Footer() const {
    switch (mode_) {
        case Mode::kLearn:
            return "上/下换词 · OK复习 · 长按返回";
        case Mode::kRecallQuestion:
            return "想好意思 · OK揭示 · 上/下放弃";
        case Mode::kRecallAnswer:
            return "上=记得 下=忘了 · OK跳过";
        case Mode::kStats:
            return "任意键返回学习";
    }
    return "";
}
