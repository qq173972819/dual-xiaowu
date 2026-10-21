#pragma once

#include <functional>
#include <string>
#include <vector>

// Flashcard word trainer, adapted from the open-source Word Box
// (github.com/hyrographic/word_of_the_day_public): a daily word card,
// a recall mode that hides the definition, remembered/forgot scoring,
// a day streak and per-day statistics.
//
// Word list comes from the TF card (/xiaozhi/words/words.txt, one word per
// line: word,phonetic,pos,meaning[,example]); progress is persisted in NVS.
class WordMachine {
public:
    enum class Mode { kLearn, kRecallQuestion, kRecallAnswer, kStats };

    // The word list is read through this loader so the machine stays
    // decoupled from SdCardStorage (wired up by the board).
    void SetWordListLoader(std::function<std::string()> loader);

    // Loads the word list + persisted history once; safe to call repeatedly.
    void EnsureLoaded();
    bool loaded() const { return loaded_; }

    Mode mode() const { return mode_; }

    // Up/down keys. Meaning depends on the mode: browse cards, cancel the
    // recall, score "remembered/forgot", or leave the stats view.
    void Next();
    void Previous();
    // OK key: start a recall, reveal the answer, skip scoring, or close
    // the stats view.
    void Enter();

    std::string Title() const;
    std::string Body() const;
    std::string Footer() const;

private:
    struct Entry {
        std::string word;
        std::string phonetic;
        std::string pos;
        std::string meaning;
        std::string example;
    };
    struct DayRecord {
        std::string date;  // YYYY-MM-DD
        int correct = 0;
        int wrong = 0;
    };
    struct RecentWord {
        std::string word;
        std::string date;  // YYYY-MM-DD, last studied
    };

    bool loaded_ = false;
    Mode mode_ = Mode::kLearn;
    std::vector<Entry> entries_;
    size_t cursor_ = 0;
    size_t today_index_ = 0;
    std::vector<DayRecord> history_;
    std::vector<RecentWord> recent_;
    size_t recall_index_ = 0;
    int recall_days_ago_ = 0;
    std::string message_;  // transient feedback line on the learn card

    std::function<std::string()> word_list_loader_;

    void ParseWordList(const std::string& text);
    void LoadHistory();
    void SaveHistory() const;
    void LoadRecent();
    void SaveRecent() const;
    void RecordSeen(const std::string& word);
    void RecordScore(bool correct);
    bool PickRecallWord();
    int Streak() const;
    void TodayTotals(int& correct, int& wrong) const;
    static std::string TodayString();
};
