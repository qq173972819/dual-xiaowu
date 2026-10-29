from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from PIL import Image

ROOT=Path(r"E:\electron\screen\inksight-main\inksight-main\xiaowu-s3-open-platform")
OUT=ROOT/"output/whitepaper"; OUT.mkdir(parents=True,exist_ok=True)
PDF=OUT/"小悟AI墨水屏产品白皮书.pdf"
W,H=A4; M=48
NAVY=HexColor("#14324A"); TEAL=HexColor("#2A8C82"); GRAY=HexColor("#5D6670"); LIGHT=HexColor("#F4F6F7"); LINE=HexColor("#D9D9D9")
pdfmetrics.registerFont(TTFont("CN",r"C:\Windows\Fonts\msyh.ttc")); pdfmetrics.registerFont(TTFont("CNB",r"C:\Windows\Fonts\msyhbd.ttc"))
c=canvas.Canvas(str(PDF),pagesize=A4)

def txt(s,x,y,size=10,color=GRAY,font="CN",maxw=None,leading=None):
    c.setFont(font,size); c.setFillColor(color); leading=leading or size*1.5
    if not maxw: c.drawString(x,y,s); return y-leading
    lines=[]; cur=""
    for ch in s:
        if pdfmetrics.stringWidth(cur+ch,font,size)>maxw and cur: lines.append(cur); cur=ch
        else: cur+=ch
    if cur: lines.append(cur)
    for line in lines: c.drawString(x,y,line); y-=leading
    return y

def img(path,x,y,w,h=None):
    path=Path(path)
    if not path.exists(): return
    im=Image.open(path); iw,ih=im.size
    if h is None: h=w*ih/iw
    scale=min(w/iw,h/ih); dw,dh=iw*scale,ih*scale
    c.drawImage(ImageReader(im),x+(w-dw)/2,y+(h-dh)/2,dw,dh,mask='auto')

def header(kicker,title,sub=""):
    txt(kicker,M,H-48,8,TEAL,"CNB"); txt(title,M,H-78,20,HexColor("#000000"),"CNB")
    y=H-102
    if sub: y=txt(sub,M,y,9.5,GRAY,"CN",W-2*M,14)
    return y-8

def footer(n):
    c.setStrokeColor(LINE); c.line(M,32,W-M,32); txt(f"小悟 AI 墨水屏产品白皮书  ·  {n}",M,20,7.5,GRAY)

def new(n): footer(n); c.showPage()

def table(y,headers,rows,widths,rowh=38):
    x=M; total=sum(widths); c.setStrokeColor(LINE); c.setLineWidth(.6)
    c.setFillColor(NAVY); c.rect(x,y-rowh,total,rowh,fill=1,stroke=0)
    xx=x
    for i,h in enumerate(headers):
        txt(h,xx+6,y-24,8,white,"CNB"); xx+=widths[i]
    y-=rowh
    for ri,row in enumerate(rows):
        if ri%2: c.setFillColor(LIGHT); c.rect(x,y-rowh,total,rowh,fill=1,stroke=0)
        xx=x
        for i,val in enumerate(row):
            c.rect(xx,y-rowh,widths[i],rowh,fill=0,stroke=1)
            txt(str(val),xx+6,y-14,7.5,HexColor("#26343D"),"CN",widths[i]-12,10)
            xx+=widths[i]
        y-=rowh
    return y-12

def bullets(y,items):
    for s in items:
        c.setFillColor(TEAL); c.circle(M+4,y+3,2.3,fill=1,stroke=0)
        y=txt(s,M+14,y,9,HexColor("#26343D"),"CN",W-2*M-14,13)-3
    return y

logo=ROOT/"logo1-xiaowu-ai.png"; hero=ROOT/"firmware/s3/docs/xiaowu/voice-note-product.jpg"; e6=ROOT/"firmware/s3/docs/xiaowu/seven-inch-e6-display.jpg"; board=ROOT/"firmware/s3/docs/xiaowu/dual-screen-mainboard.jpg"; c3=ROOT/"firmware/s3/docs/v1/xmini-c3.jpg"; daily=ROOT/"services/legacy-api/docs/design/daily-800x480-after.png"; arch=OUT/"技术架构.png"; nfc=Path(r"C:\Users\upisq\AppData\Local\Temp\browser-use\assets\856ec2cf-e77d-4e00-9a4b-eb9e94fa0919\93d86fee8eac0ee1.jpg")

# 1 cover
img(logo,W/2-40,H-105,80,44); txt("小悟 AI 墨水屏产品白皮书",M,H-165,26,HexColor("#000000"),"CNB"); txt("低功耗常显设备与 AI 内容生态的一体化产品体系",M,H-195,12,GRAY)
img(hero,M,190,W-2*M,390); txt("沄墨电子科技(四川)有限责任公司",M,155,11,NAVY,"CNB"); txt("2026 年 10 月  产品与技术概览",M,135,8.5,GRAY); new(1)

y=header("01  白皮书摘要","让信息安静地留在眼前","小悟以电子纸为长期可见的信息载体，把设备、内容、AI、后台和家庭物联网连接为同一套可扩展系统。")
y=table(y,["核心价值","产品实现","客户收益"],[["低功耗常显","刷新时主要耗电，断电仍保留画面","减少发光屏依赖"],["AI 内容生成","连接大模型与场景数据","形成个性内容"],["软硬件一体化","设备、固件、后台与模式协同","支持定制和量产"],["多形态覆盖","1.54 至 10 英寸多色电子纸","覆盖个人到商业展示"]],[100,190,200],44)
txt("产品体系结论",M,y,11,NAVY,"CNB"); y-=20
y=txt("小悟并非单一电子纸硬件，而是一套围绕常显信息、AI 内容和设备管理构建的产品生态。统一后台可管理设备分组、内容模板与刷新计划。",M,y,9.5,GRAY,"CN",W-2*M,14)-8
bullets(y,["4 类核心产品系列，5 类可选显示尺寸。","C3 侧重静默低功耗，S3 扩展语音与学习陪伴。","支持在线刷机、设备配置、无设备预览、模式广场和自定义内容模式。"]); new(2)

y=header("02  产品目录","四类产品覆盖从桌面到空间展示","量产产品、研发版本与定制方案，以最终交付规格为准。")
y=table(y,["产品系列","典型规格","交互方式","主要场景"],[["C3 静默摆件","4.2 / 5.83 英寸","Wi-Fi","桌面信息、家庭看板"],["S3 语音陪伴","单屏 / 双屏","语音、按键","教育、阅读、健康"],["NFC / 蓝牙挂件","1.54 英寸四色","NFC + 蓝牙","随身与近场信息"],["大尺寸全彩","7 / 10 英寸 E6","Wi-Fi / 语音","空间与商业展示"]],[110,135,95,150],42)
img(e6,120,150,355,355); txt("7 英寸 E6 全彩电子纸实机示例",190,140,8,GRAY); new(3)

y=header("03  ESP32 C3 静默摆件","桌面上的多场景 AI 搭档","在线刷机、设备配置、无设备预览与模式广场共同构成低门槛使用路径。")
img(c3,120,390,355,280); y=370
y=table(y,["能力","说明"],[["AI 驱动","根据天气、时间与场景生成个性化内容"],["电子墨水","类纸显示、不主动发光，适合长时间常驻"],["模式生态","内置 24 类内容模式，支持自定义与分享"],["低功耗设计","RISC-V、Wi-Fi、深度休眠与电池供电"],["开发支持","Arduino、ESP-IDF、PlatformIO 与在线刷机"]],[120,370],38); new(4)

y=header("04  ESP32 S3 语音陪伴终端","会开口的墨水屏陪伴每一次练习","单屏与双屏两种形态，将低干扰显示与 AI 语音交互结合。")
img(hero,95,370,405,330); y=350
y=table(y,["模块","典型能力"],[["语音交互","麦克风、扬声器、AI 对话与语音唤醒"],["学习模式","口语陪练、阅读、单词、课程表、学情分析"],["显示形态","单屏菜单；双屏主菜单与副屏协同"],["云端协同","用户、内容、记录和模型服务按需连接"]],[120,370],42); new(5)

y=header("05  NFC 蓝牙挂件与全彩系列","从随身近场到大尺寸空间展示","不同连接方式与色彩能力，让信息出现在更合适的位置。")
img(nfc,55,390,230,280); img(e6,310,390,230,280); txt("1.54 英寸 NFC 场景",90,378,8,GRAY); txt("大尺寸全彩电子纸",350,378,8,GRAY)
y=table(350,["系列","优势","典型用途"],[["NFC / 蓝牙挂件","近场更新、体积小","冰箱贴、钥匙扣、随身提醒"],["7 / 10 英寸 E6","大画幅、丰富色彩","艺术画框、门店与企业展示"]],[125,165,200],48)
txt("电子纸的价值不只在省电，更在于画面能够脱离持续供电而保留。",M,y,9.5,GRAY,"CN",W-2*M,14); new(6)

y=header("06  内容模式生态","内容随生活持续生长","预设模式、AI 生成与自定义模式共同组成可持续扩展的内容供给。")
img(daily,80,410,435,260); y=390
y=table(y,["类别","代表模式"],[["每日信息","每日、天气、简报、今日历史、老黄历"],["文化与思考","诗词、斯多葛、禅意、认知偏差、每日一问"],["生活与习惯","食谱、倒计时、便签、打卡、健身、人生进度条"],["创作与互动","画廊、慢信、微故事、每日一谜、微挑战、毒舌"],["开放定制","组合文本、图片、数据源、模板与刷新规则"]],[130,360],40)
txt("官网列出 23 个具名模式加自定义模式，共 24 类内置内容能力。",M,y,9,GRAY); new(7)

y=header("07  开放平台与设备运营","从一次交付走向持续运营","统一后台把设备、内容、用户、计划和场景组织为可管理的产品体系。")
y=table(y,["平台模块","核心职责","面向对象"],[["设备管理","绑定、分组、状态、版本与远程配置","运营、渠道、客户管理员"],["内容后台","模板、素材、数据源与多尺寸适配","内容运营与业务人员"],["模式广场","发现、预览、分享与复用模式","用户与创作者"],["在线刷机与配置","固件烧录、网络与设备参数配置","用户、售后与研发"],["开放接口","AI、家庭物联网和业务系统连接","开发者与伙伴"]],[115,245,130],45)
txt("端到端运行流程",M,y,11,NAVY,"CNB"); y-=20
bullets(y,["设备注册并上报屏幕和固件能力。","后台选择模式、数据源、刷新计划与设备分组。","内容服务生成数据，完成排版与图像处理。","设备获取更新，刷新电子纸并进入低功耗状态。","运营侧根据设备状态和内容效果持续调整。"]); new(8)

y=header("08  技术架构","端云协同的五层产品架构","硬件、连接、平台、AI 内容与应用场景通过统一设备模型和内容模型协同。")
img(arch,M,175,W-2*M,540); txt("设备侧管理显示、音频、按键、存储与低功耗；云端负责设备、模式、内容和用户服务。连接层兼容 Wi-Fi、蓝牙、NFC，并可按项目接入 Home Assistant、HTTPS、MQTT 与 OTA。",M,155,8.5,GRAY,"CN",W-2*M,12); new(9)

y=header("09  场景解决方案","同一平台适配多种空间与人群","以常显、低打扰和可远程更新为共同基础，按场景组合硬件与内容。")
table(y,["场景","解决方案组成","价值"],[["家庭物联网","Home Assistant + 状态卡片 + 语音","集中查看与控制家庭设备"],["共享家庭看板","日历、待办、照片与远程更新","跨代共享家庭信息"],["学习终端","快刷、单词卡、电子书与作业推送","降低发光屏干扰"],["教育陪伴","语音、口语、阅读、课程与学情","集中练习和反馈"],["文化探索","易经、紫微、星座与知识卡片","承载日更与个性内容"],["商业展示","全彩屏、设备分组与定时刷新","融入门店与办公空间"]],[110,240,140],53); new(10)

y=header("10  核心技术与交付能力","从开发板到量产定制","三套开发板方向与统一软件平台支持原型、试制和批量交付。")
img(board,80,390,435,280); y=370
table(y,["技术方向","能力摘要"],[["硬件平台","ESP32-C3、S3 无语音版、S3 语音版"],["显示适配","1.54、4.2、5.83、7.3、10 英寸及多色电子纸"],["固件工程","Arduino、ESP-IDF、PlatformIO、在线刷机、OTA、低功耗"],["内容工程","模板布局、图像处理、多尺寸渲染、AI 生成"],["项目交付","咨询、样机、批量采购、软硬件定制与平台集成"]],[120,370],40); new(11)

y=header("11  公司与团队","以电子纸产品化连接 AI 与真实空间","团队聚焦电子纸终端、嵌入式系统、AI 内容平台与场景化产品落地。")
txt("公司主体",M,y,11,NAVY,"CNB"); y-=22; txt("沄墨电子科技(四川)有限责任公司",M,y,10,GRAY); y-=38
txt("创始人",M,y,11,NAVY,"CNB"); y-=22; txt("张少敏",M,y,10,GRAY); y-=38
txt("团队背景",M,y,11,NAVY,"CNB"); y-=22; y=txt("核心团队成员背景涵盖武汉电子研究所、东南大学硕士与中国科学技术大学博士，在电子工程、嵌入式开发、人工智能与产品工程化方面形成交叉能力。",M,y,9.5,GRAY,"CN",W-2*M,15)-18
txt("合作方向",M,y,11,NAVY,"CNB"); y-=22; y=bullets(y,["小悟系列产品采购与渠道合作","教育、家庭、健康与商业展示解决方案","电子纸开发板、固件与开放平台合作","样机验证、联合研发与软硬件定制"])
img(logo,W/2-45,110,90,55); txt("官方网站  https://xiaowuai.vip/zh",185,88,9,TEAL,"CNB"); txt("产品规格、功能、价格和交付计划以正式合同及最终发布版本为准。",125,65,7.5,GRAY); footer(12)
c.save(); print(PDF)
