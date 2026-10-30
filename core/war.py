#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""War / Spam tag trong nhóm — mỗi lượt tag 1 người kèm 1 câu chửi soạn sẵn.

• /war @user [số_lần] [giãn_cách]  — war liên tục, mỗi lượt 1 câu (mặc định 30 lượt)
• /spam @user [số_lần] [giãn_cách] — spam tag nhanh (mặc định 20 lượt)
• /stop                            — dừng ngay war/spam đang chạy trong chat đó

Giãn cách chỉnh được: 0.5 · 0.5s · 800ms · 1.2 · 2s (mặc định 1s, sàn 0.4s để
không bị Telegram chặn). Gặp RetryAfter thì tự nghỉ đúng thời gian Telegram yêu cầu.
Chỉ admin bot hoặc admin nhóm mới bật được.
"""

import asyncio
import html
import logging
import random
import time

from core import premium

log = logging.getLogger("khoaan.war")

MIN_INTERVAL = 0.4
MAX_INTERVAL = 10.0
DEFAULT_WAR = 30
DEFAULT_SPAM = 20
MAX_TURNS = 100

# ─────────────────────────────────────────────── 200 câu chửi soạn sẵn
LINES: list[str] = [
    # ── độ đần
    "não bằng hạt đậu mà cứ tưởng mình thiên tài 🤡",
    "nói chuyện với bạn mà tôi tưởng đang đọc quảng cáo rao vặt 💩",
    "IQ của bạn chắc phải đo bằng đơn vị âm 📉",
    "bạn là bằng chứng sống rằng wifi mạnh không giúp được gì 🤖",
    "suy nghĩ kỹ rồi hẵng nói, mà thôi đừng nghĩ nữa 💀",
    "đầu bạn chắc chứa toàn bông gòn chứ không có não 🧠",
    "bạn vừa nói gì thế, tôi cần thuốc đau đầu 💊",
    "nói xong tự thấy mình sai chưa, hay vẫn tưởng mình đúng 👍",
    "có ai hỏi mà bạn trả lời như tra tấn vậy không 💀",
    "bạn đang nghĩ hay đang buff lag vậy 👀",
    "câu này bạn tự nghĩ ra hay bấm nhầm nút 🤡",
    "mỗi lần bạn mở miệng là một lần tôi mất niềm tin vào loài người 💩",
    "bạn có biết vì sao Google không tìm được bạn không, vì nó cũng chịu 🔍",
    "cãi thì giỏi mà hiểu thì không, đúng chuẩn chuyên gia mõm 💬",
    "bạn nói như kiểu đang đọc từ giấy, mà giấy đó cũng viết sai 💀",
    "muốn khôn ra thì đừng cố tỏ ra khôn nữa 🤡",
    "trí tuệ của bạn đang ở chế độ tiết kiệm pin 🔋",
    "bạn suy nghĩ chậm đến mức tôi kịp nấu xong nồi cơm 🍚",
    "nói chậm thôi, não bạn không theo kịp tay đâu 💀",
    "không phải bạn dốt, chỉ là bạn rất giỏi việc tỏ ra dốt 🤡",
    "bạn là bản beta của một con người 🤖",
    "bạn vừa chứng minh một điều: không cần não vẫn gõ phím được ⌨️",
    "lý luận của bạn cong như sợi mì gói 🍜",
    "nói 3 câu sai 4 lỗi, tài năng đấy 👏",
    "bạn đang cố gắng hay đang cố tình vậy 🤡",
    "cái đầu đó để đội nón chứ không phải để nghĩ 🎩",
    "bạn nghĩ nhanh thật, mà nghĩ sai còn nhanh hơn ⚡",
    "đừng lo, ai cũng có điểm yếu, điểm yếu của bạn là tư duy 💀",
    "bạn nói nhiều thế mà vẫn chưa nói được câu nào ra hồn 💬",
    "tôi hỏi thật, bạn có tự đọc lại tin mình gửi không 👀",

    # ── vô dụng / ăn hại
    "vô dụng đến mức cái ghế cũng thấy tự hào khi được ngồi 🪑",
    "có bạn trong nhóm thì nhóm đông hơn, chỉ vậy thôi 📊",
    "bạn tồn tại để làm nền cho người khác tỏa sáng ✨",
    "ăn hại thì giỏi, mà ăn hại cũng cần kỹ năng đấy 🍽",
    "cái nhóm này thiếu gì người, thiếu đúng một người biết im 👀",
    "bạn là phụ kiện, không phải nhân vật chính 🎭",
    "đóng góp lớn nhất của bạn cho nhóm là… có mặt 📍",
    "bạn mà làm được việc thì tôi đã đổi nghề 🌙",
    "giao việc cho bạn khác nào đưa tiền cho đứa trẻ đi chợ 💸",
    "bạn giống cái app lỗi, mở lên là muốn tắt 📱",
    "có bạn hay không có bạn, kết quả vẫn vậy, chỉ khác là ồn hơn 🔊",
    "bạn là kiểu người vào bếp rồi hỏi bát ở đâu 🍳",
    "cái nút bấm cũng biết phản hồi, bạn thì không 🔘",
    "bạn như cái bánh xe thứ năm, thừa mà vẫn phải mang 🛞",
    "ai giao việc cho bạn chắc kiếp trước nợ gì đó 💀",
    "nói chuyện với bạn xong tôi thấy mình giỏi hơn hẳn 💎",
    "bạn là bài kiểm tra lòng kiên nhẫn của cả nhóm ⏳",
    "cái vai trò của bạn trong nhóm là trang trí 🖼",
    "bạn có mặt ở đây chắc do nhóm bấm nhầm duyệt 🤖",
    "đừng buồn, làm nền cũng là một nghề 🎨",
    "bạn như đèn đường ban ngày, có cũng được không có cũng được 💡",
    "cứ im lặng là bạn đã giúp nhóm rất nhiều rồi 🤐",
    "bạn cần được khen hay cần được nhắc lại lần nữa 👀",
    "bạn giỏi nhất ở khoản làm người khác mệt 💀",
    "cái sự tồn tại của bạn đang làm chậm tiến độ cả vũ trụ 🌍",
    "bạn là kiểu người đến muộn rồi than sao không ai chờ ⏰",
    "nói thật, bạn đang làm gì ở đây vậy 📍",
    "mỗi lần bạn nhận việc, cả nhóm nín thở 😨",
    "bạn là minh chứng cho câu: sống để cho vui 🎈",
    "không cần bạn giúp đâu, chỉ cần bạn đừng cản 🤝",

    # ── nghèo / tiền
    "ví bạn mỏng đến mức đựng tiền cũng thấy ngại 💸",
    "nghèo mà còn sĩ, combo huyền thoại 🏆",
    "bạn tiết kiệm thật đấy, tiết kiệm cả lời cảm ơn 💰",
    "ví bạn rỗng như lời hứa của bạn 🕳",
    "đi ăn mà chia tiền kiểu đó thì thôi ở nhà ăn mì 🍜",
    "bạn xin tiền hay xin lỗi vậy, nghe giống xin lỗi hơn 💀",
    "có tiền đâu mà đòi sang, nhìn cái ví là biết 📉",
    "bạn tiêu tiền như người sắp hết hạn dùng, à mà có tiền đâu 💸",
    "cái nghèo không đáng xấu hổ, nhưng cái sĩ diện của bạn thì có 💀",
    "bạn khoe giàu kiểu đó mà tôi tưởng đang xin tài trợ 💰",
    "tài sản lớn nhất của bạn là lòng tự trọng hơi cao 💎",
    "bạn nói chuyện tiền bạc mà mắt nhìn xuống ví, đau không 💸",
    "đi chơi với bạn tôi hiểu cảm giác làm từ thiện 🤲",
    "bạn nghèo thật hay giả nghèo để khỏi trả tiền 🧾",
    "cái thẻ đó quẹt được không hay chỉ để cho có 💳",
    "bạn tiết kiệm được đồng nào chưa, hay chỉ tiết kiệm lời ✂️",
    "nói chuyện triệu đô mà ví còn không có nghìn 📉",
    "bạn mượn tiền mà quên trả nhanh thật, tài đó 💀",
    "cái sự nghèo của bạn không sai, sai là bạn không biết 💸",
    "bạn ăn mặc như ông chủ mà tính tiền như ăn xin 🧾",
    "ví mỏng thì nói ít thôi, đừng tỏ ra giàu 💰",
    "bạn chi tiêu có kế hoạch ghê, kế hoạch là xin 🤲",
    "thôi đừng khoe, khoe xong tôi lại thấy tội 💀",
    "bạn nghèo mà vẫn tự tin được, đó cũng là tài năng 💎",
    "có ai nợ bạn không, hay bạn nợ cả nhóm 💸",

    # ── ế / tình cảm
    "ế lâu năm nên nói chuyện có mùi cô đơn 💔",
    "bạn tán ai cũng fail, chắc do duyên chưa tới, hoặc do bạn 💀",
    "nhắn tin kiểu đó thì ế là đúng rồi 📱",
    "có người yêu chưa, hay vẫn đang yêu bản thân quá đà 💖",
    "bạn không cô đơn, bạn chỉ khó ưa thôi 🌙",
    "ế thì nói là ế, đừng nói là đang tận hưởng tự do 🎈",
    "bạn nhắn tin cho crush chưa, hay vẫn đang soạn từ năm ngoái 📅",
    "tán tỉnh kiểu đó người ta block là còn nhẹ 🚫",
    "bạn có người yêu không, hay chỉ có mèo và nỗi buồn 🐱",
    "cô đơn không đáng sợ, đáng sợ là bạn vẫn tưởng mình có cửa 💀",
    "bạn thả thính kiểu đó chỉ có cá chết nổi lên 🐟",
    "nói chuyện tình cảm mà nghe như đang báo cáo tài chính 💀",
    "crush bạn rep chưa, hay đang bận sống cuộc đời không có bạn 👀",
    "bạn yêu đương kiểu đó thì ở vậy cho lành 🌙",
    "ế không sao, nhưng đừng lấy ế ra làm tính cách 💔",
    "bạn tỏ tình chắc người ta tưởng bị đe dọa 😨",
    "có người thích bạn chưa, hay chỉ có mẹ bạn thích bạn 💖",
    "bạn nhắn 10 tin được rep 1 câu, hiểu rồi đó 📉",
    "bạn đang độc thân vì bạn xứng đáng với điều tốt hơn, đùa đấy 💀",
    "tình cảm của bạn giống sóng wifi nhà hàng xóm, xa và yếu 📶",
    "bạn có crush không, hay crush bạn từ lâu rồi 👀",
    "nói chuyện với bạn xong tôi hiểu vì sao bạn ế 💔",
    "bạn tán ai cũng bảo để làm bạn, đau thật đấy 💀",
    "ế mà vẫn kén, chuẩn luôn 💎",
    "yêu thì ít mà đòi hỏi thì nhiều, chuẩn bài 💔",

    # ── ngoại hình / tự tin
    "soi gương chưa, hay gương nó cũng tránh bạn 🪞",
    "bạn tự tin thật đấy, tự tin quá mức là bệnh 💀",
    "mặt mũi thì thường mà thái độ thì như hoa hậu 👑",
    "bạn ăn mặc như này ra đường có ai hỏi thăm chưa 👕",
    "cái đầu tóc đó là tự cắt hay bị trả thù 💇",
    "bạn đứng cạnh ai cũng làm người ta đẹp hơn, đó cũng là đóng góp ✨",
    "style của bạn rất riêng, riêng đến mức không ai hiểu 🎨",
    "bạn cười tươi thật, nhưng đừng cười nữa 😀",
    "body đó chắc tập bằng cách ngồi nhiều lắm 💪",
    "bạn đẹp trai không, hay chỉ tự nghĩ vậy 🪞",
    "cái mùi tự tin của bạn bay xa quá 🌬",
    "bạn thử hỏi người khác xem họ nghĩ gì về bạn đi, dũng cảm lên 🎤",
    "ăn mặc đẹp mà mặt không cứu được 👔",
    "bạn có biết nước hoa không, hay chỉ dùng mùi cơ thể 🌡",
    "kiểu tóc đó chắc mới ngủ dậy cả năm rồi 💇",
    "bạn tự tin đến mức tôi cũng thấy hơi sợ 😨",
    "cái gu của bạn độc thật, độc hại 💀",
    "bạn cười lên trông khác hẳn, xấu hẳn 😀",
    "nhìn lại mình đi, rồi hẵng nói người khác 🪞",
    "bạn trang điểm đậm thật, chắc để che cái tính cách 💄",
    "không phải ai cũng hợp với kiểu đó đâu, nhất là bạn 👀",
    "cái gương nhà bạn chắc mệt mỏi lắm 🪞",
    "bạn có bao giờ tự hỏi sao ảnh chụp mình khác ngoài đời không 📸",
    "tự tin là tốt, nhưng bạn thì hơi lố 💀",
    "bạn đẹp trong mắt mẹ bạn, đó là đủ rồi 💖",

    # ── nói nhiều / chém gió
    "nói ít thôi, nói nhiều không làm bạn thông minh hơn đâu 💬",
    "bạn chém gió đến mức tôi phải bật quạt 🌬",
    "nói hay lắm, làm được câu nào chưa 💀",
    "bạn nói như sách, mà sách đó là sách bài tập sai ✏️",
    "miệng bạn nhanh hơn não bạn, nguy hiểm đấy 💬",
    "nói một câu mà tôi mất 3 phút để hiểu bạn muốn gì ⏳",
    "bạn nói nhiều như quảng cáo giữa phim 📺",
    "hứa thì nhiều, làm thì ít, chuẩn mẫu người 💬",
    "bạn hứa câu đó mấy lần rồi, đếm được không 📊",
    "nói chuyện với bạn tôi hiểu cảm giác bị tra tấn bằng chữ 💀",
    "bạn dùng từ to thế mà nghĩa thì nhỏ 🧾",
    "nói thì hay, đến lúc cần thì biến mất 👀",
    "bạn có biết im lặng là vàng không, thử đi 💎",
    "chém gió thì bạn vô địch, làm thật thì vô hình 🏆",
    "bạn nói mình giỏi, vậy chắc chưa ai kiểm tra 💀",
    "khoe khoang kiểu đó người ta gọi là tự sướng 📸",
    "bạn nói xong chưa, tôi hết kiên nhẫn rồi ⏳",
    "nói 10 câu không có câu nào có số liệu, chuẩn chuyên gia 📊",
    "bạn đang cãi hay đang tự nói với mình 💬",
    "tôi không cần bạn giải thích, tôi cần bạn im 🤐",
    "bạn nói chuyện như súng máy, chỉ khác là không trúng gì 💥",
    "bạn đổi chủ đề nhanh thật, chắc vì lý lẽ yếu 🤡",
    "mỗi câu của bạn đều là một lời hứa suông 💬",
    "bạn nói tôi nghe đi, rồi quên luôn giùm tôi 🙏",
    "nói nhiều vậy mà không ai hỏi bạn 💀",

    # ── lười / chậm
    "bạn chậm đến mức tôi tưởng bạn đang tải trang 📶",
    "lười kiểu đó thì thua cả cái ghế 🪑",
    "bạn làm gì cũng chậm, trừ việc ăn 🍚",
    "gọi bạn 3 lần mới nghe, chắc não bạn dùng 2G 📶",
    "bạn phản hồi chậm thật, chắc phải gửi thư tay ✉️",
    "việc nhỏ vậy mà cũng kêu mệt, tài thật 💀",
    "bạn ngủ nhiều thế mà vẫn mệt, chắc do ngủ sai cách 😴",
    "bạn có làm gì đâu mà mệt, hay mệt vì làm nũng 💤",
    "chậm như vậy thì đừng nhận việc gấp ⏳",
    "bạn lười đến mức cái báo thức cũng bỏ cuộc ⏰",
    "bạn đến muộn kiểu đó chắc có lý do chính đáng, đùa đấy ⏰",
    "cái ghế của bạn chắc cũng muốn nghỉ việc 🪑",
    "bạn nằm nhiều thế thì thử đứng lên xem, vẫn sống được đấy 💪",
    "bạn làm chậm mà còn sai, combo hoàn hảo 💀",
    "cứ từ từ rồi cũng tới, mà bạn thì tới trễ ⏳",
    "bạn có biết deadline là gì không, hay chỉ biết deadline của người khác 📅",
    "cái tốc độ đó mà chạy grab thì khách bỏ đi bộ hết 🚶",
    "bạn nói mình bận, bận gì, bận nằm à 😴",
    "bạn mà chăm chỉ thì thiên hạ đã hết người lười 🌍",
    "bạn đang làm việc hay đang giả vờ làm việc 👀",
    "chậm thế này thì mùa sau mới xong 🐢",
    "bạn có bao giờ đúng giờ chưa, hay đó là truyền thuyết ⏰",
    "bạn nghỉ ngơi nhiều thế mà vẫn chưa khoẻ, lạ thật 😴",

    # ── game / online
    "chơi game kiểu đó thì xoá game giùm bạn luôn 🎮",
    "rank đó là rank của người ta chơi hộ đúng không 🏆",
    "bạn feed xong rồi đổ lỗi cho mạng, chuẩn bài 📶",
    "bạn có biết chơi không, hay chỉ cầm chuột cho có 🖱",
    "thua thì nhận đi, đừng kể lể như phim 💀",
    "bạn ping cao hơn trình độ, đúng rồi 📈",
    "bạn vào trận là cả team thở dài 😮‍💨",
    "kỹ năng của bạn nằm ở phần chat, không phải phần chơi 💬",
    "bạn kêu lag từ đầu trận đến cuối trận 📶",
    "bạn chơi hay thật, hay là hay chửi 🎮",
    "bạn đánh xong trận rồi rút ra bài học gì chưa, chắc chưa 📖",
    "xem bạn chơi tôi hiểu vì sao người ta thích xem stream 📺",
    "bạn giỏi nhất là bấm surrender 🏳",
    "bạn nói mình carry, carry cái gì, carry nỗi buồn à 💀",
    "team bạn thắng chắc nhờ may, không nhờ bạn 🍀",
    "bạn online cả ngày mà chẳng làm được gì 🌐",
    "bạn comment nhanh thật, nghĩ thì chậm 💬",
    "bạn dùng mạng để làm gì vậy, để nói nhảm à 📶",
    "bạn gõ phím nhanh thật, tiếc là nội dung rỗng ⌨️",
    "bạn có bao giờ offline chưa, hay sợ bỏ lỡ tin nhắn 📱",
    "bạn nói mình pro, pro ở khoản nói 🏆",
    "bạn xem lại replay đi, rồi tự hiểu 🎬",
    "bạn thua vì trình, không phải vì đồng đội 💀",
    "bạn lập team kiểu đó thì team nào dám nhận 🤝",
    "bạn làm gì cũng live, trừ việc học 📡",

    # ── chốt
    "nói chung là bạn dễ thương, đùa đấy 💀",
    "thôi bớt nói lại giùm cái, tôi mệt rồi 😮‍💨",
    "bạn cần được ôm không, hay cần được nhắc lại 🤗",
    "bạn nên im lặng 5 phút, thử xem có ai nhớ bạn không 👀",
    "câu này tặng bạn: bớt ảo tưởng, thêm hành động 💎",
    "bạn cứ sống như vậy đi, tôi không cản nữa 🤷",
    "hết chuyện để nói rồi, giờ đến lượt bạn tự ngẫm 🪞",
    "bạn đọc tới đây rồi vẫn tưởng mình đúng, đúng là bạn 💀",
    "thôi, tôi nhường bạn phần đúng, phần còn lại bạn tự lo 👍",
    "chúc bạn sớm khôn ra, muộn cũng được, miễn là khôn ⚡",
]

# bộ ngắn dùng cho /spam (tag nhanh, không cần câu dài)
SHORT = [
    "dậy đi bạn ơi 💀", "ngủ đủ chưa 👀", "có nghe không 📢", "trả lời đi 🤡",
    "nói gì đi 💬", "im gì dữ vậy 🤐", "online không 🌐", "alo 📞",
    "bạn ơi 🔔", "có ai ở nhà không 🏠", "rép đi mà 🙏", "sao im 👀",
    "bạn đâu rồi 📍", "lên tiếng coi 🔊", "nghe thấy không 📶", "thấy tin chưa 📱",
]


# emoji đã có id premium thật trong core/premium.py -> dùng để bảo đảm tin nào
# cũng có ít nhất 1 icon premium, kể cả khi câu chửi chỉ có emoji thường.
PREMIUM_EMOJI = ["⚡", "🔥", "💎", "👀", "😀", "🎁", "💖", "✅", "❌", "💬", "👑", "🎭", "⭐", "🐢"]


def decorate(line: str) -> str:
    """Thêm 1 icon premium nếu câu chưa có emoji nào thuộc bộ premium."""
    if any(e in line for e in PREMIUM_EMOJI):
        return line
    return f"{line} {random.choice(PREMIUM_EMOJI)}"


def random_line(short: bool = False) -> str:
    return decorate(random.choice(SHORT if short else LINES))


def mention_of(user) -> str:
    if user is None:
        return ""
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = html.escape(user.first_name or user.full_name or "bạn")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# ─────────────────────────────────────────────── engine
_active: dict[int, dict] = {}


def is_running(chat_id: int) -> bool:
    job = _active.get(chat_id)
    return bool(job and not job["task"].done())


def info(chat_id: int) -> dict:
    job = _active.get(chat_id)
    if not job:
        return {}
    return {
        "kind": job["kind"],
        "to": job["mention"],
        "sent": job["sent"],
        "total": job["total"],
        "by": job["by"],
    }


async def stop(chat_id: int) -> int:
    job = _active.pop(chat_id, None)
    if not job:
        return -1
    job["task"].cancel()
    try:
        await asyncio.sleep(0)
    except Exception:  # noqa: BLE001
        pass
    return job["sent"]


async def stop_all() -> int:
    """Dừng mọi war/spam đang chạy (dùng khi bot tắt)."""
    n = 0
    for chat_id in list(_active):
        if await stop(chat_id) >= 0:
            n += 1
    return n


async def _loop(bot, chat_id: int, mention: str, total: int, interval: float,
                kind: str, by: int) -> None:
    sent = 0
    job = _active[chat_id]
    try:
        for _ in range(total):
            text = f"{mention} {random_line(kind == 'spam')}"
            try:
                await bot.send_message(
                    chat_id, premium.ensure(text), parse_mode="HTML",
                    disable_web_page_preview=True, read_timeout=15,
                )
                sent += 1
                job["sent"] = sent
            except Exception as e:  # noqa: BLE001
                name = type(e).__name__
                if "RetryAfter" in name:
                    wait = int(getattr(e, "retry_after", 5) or 5)
                    await asyncio.sleep(min(wait, 30))
                    continue
                log.info("war send lỗi (%s) — dừng", str(e)[:80])
                break
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("war/spam chat %s bị dừng sau %d lượt", chat_id, sent)
        raise
    finally:
        cur = _active.get(chat_id)
        if cur and cur.get("task") is job["task"]:
            _active.pop(chat_id, None)


def parse_interval(raw: str) -> float | None:
    """Đọc giãn cách người dùng gõ: 0.5 · 0.5s · 800ms · 2s · 1,2s · 1 giây."""
    if raw is None:
        return None
    t = str(raw).strip().lower().replace(",", ".")
    if not t:
        return None
    mult = 1.0
    for unit, m in (("giây", 1.0), ("giay", 1.0), ("ms", 0.001), ("s", 1.0)):
        if t.endswith(unit):
            t = t[: -len(unit)].strip()
            mult = m
            break
    try:
        val = float(t)
    except ValueError:
        return None
    return max(MIN_INTERVAL, val * mult)


def start(bot, chat_id: int, mention: str, total: int, kind: str = "war",
          interval: float = 1.0, by: int = 0) -> dict:
    """Bật war/spam. Trả {"ok": bool, "msg": str, "total": int, "interval": float}."""
    if is_running(chat_id):
        return {"ok": False, "msg": "Đang chạy rồi, gõ /stop trước.", "total": 0}
    total = max(1, min(int(total or 0) or (DEFAULT_SPAM if kind == "spam" else DEFAULT_WAR), MAX_TURNS))
    interval = min(MAX_INTERVAL, max(MIN_INTERVAL, float(interval or 1.0)))
    task = asyncio.create_task(_loop(bot, chat_id, mention, total, interval, kind, by))
    _active[chat_id] = {
        "task": task, "kind": kind, "mention": mention, "sent": 0,
        "total": total, "by": by, "t0": time.time(),
    }
    return {"ok": True, "msg": "bắt đầu", "total": total, "interval": interval}
