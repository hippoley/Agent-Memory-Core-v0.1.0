"""Build the 200-item Chinese household golden set for Agent Memory Core.

Why this exists: the public memory benchmarks (LoCoMo, LongMemEval) are
English, session-level, QA-shaped. This service stores Chinese atomic claims
(subject, predicate, value), so those benchmarks cannot exercise it without a
real LLM extractor. This set is written against the shipped `home-zh` rule
pack's *contract*, not its regexes, and is split so precision/recall stay
honest:

  A  in-vocabulary positives   — the four documented predicates
  B  polarity traps            — negation must not flip or drop the fact
  C  noise                     — must extract nothing (precision)
  D  out-of-vocabulary facts   — real domain facts the pack cannot express;
                                 reported as coverage gap, excluded from F1
  E  updates                   — same subject+predicate, changed value

Run: python scripts/eval/build_gold_home_zh.py
Out: data/eval/gold_home_zh.jsonl (200 lines)
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "eval" / "gold_home_zh.jsonl"

TEMP = "temperature_preference"
QUIET = "noise_preference"
LIGHT = "light_preference"

items: list[dict] = []


def add(kind: str, text: str, gold: list[dict], note: str = "") -> None:
    items.append({
        "id": f"g{len(items) + 1:03d}",
        "kind": kind,
        "text": text,
        "gold": gold,
        "note": note,
    })


# ---------------------------------------------------------------- A: positives
temp_lines = [
    ("主卧空调调到23度", "主卧", 23), ("客厅温度设成26度", "客厅", 26),
    ("书房暖气开到22度", "书房", 22), ("次卧空调制冷25度", "次卧", 25),
    ("厨房温度太高了，调到24度", "厨房", 24), ("把阳台的温度定在18度", "阳台", 18),
    ("儿童房空调调到27度", "儿童房", 27), ("卧室暖气22度就行", "卧室", 22),
    ("玄关空调设21度", "玄关", 21), ("卫生间暖气开25度", "卫生间", 25),
    ("主卧温度改成20度", "主卧", 20), ("客厅空调26度比较舒服", "客厅", 26),
    ("书房温度调到23度", "书房", 23), ("阳台空调设成28度", "阳台", 28),
    ("次卧暖气调到19度", "次卧", 19), ("儿童房温度设24度", "儿童房", 24),
    ("厨房空调开22度", "厨房", 22), ("卧室温度调到21度", "卧室", 21),
    ("玄关暖气26度", "玄关", 26), ("卫生间空调调到23度", "卫生间", 23),
    ("主卧空调开到24度", "主卧", 24), ("客厅暖气调到21度", "客厅", 21),
    ("书房温度26度", "书房", 26), ("次卧空调27度", "次卧", 27),
    ("厨房温度调到20度", "厨房", 20), ("阳台暖气23度", "阳台", 23),
    ("儿童房空调调到25度", "儿童房", 25), ("卧室暖气调到24度", "卧室", 24),
]
for text, subj, c in temp_lines:
    add("A", text, [{"predicate": TEMP, "value": {"celsius": c}}], f"subject={subj}")

quiet_lines = [
    "主卧保持安静", "客厅安静一点", "书房静音模式", "次卧小声些", "卧室别太吵",
    "儿童房安静些", "厨房不要吵", "阳台静音", "玄关安静一点", "卫生间小声",
    "书房声音太大了，安静些", "主卧有点吵，静音", "客厅太吵了", "次卧保持安静模式",
    "儿童房别吵", "卧室安静一点谢谢", "书房静音", "厨房小声一点", "阳台别太吵", "玄关静音模式",
]
for text in quiet_lines:
    add("A", text, [{"predicate": QUIET, "value": {"level": "quiet"}}])

dim_lines = [
    "主卧灯光调暗", "客厅灯光暗一点", "书房光线柔和些", "次卧照明不要太亮",
    "卧室灯太刺眼", "儿童房灯光调暗些", "厨房灯光别太亮", "阳台照明暗一点",
    "玄关灯太强了", "卫生间光线柔和", "主卧灯太亮", "客厅照明调暗",
    "书房灯光暗些", "次卧灯别太亮", "儿童房光线要柔和",
    "主卧灯调暗一点", "客厅灯别刺眼", "玄关灯光柔和些", "阳台灯调暗",
    "厨房照明暗一些", "卫生间灯别太亮", "次卧光线太强了",
]
for text in dim_lines:
    add("A", text, [{"predicate": LIGHT, "value": {"level": "dim"}}])

bright_lines = [
    "主卧灯光调亮", "客厅灯亮一点", "书房照明亮些", "次卧开灯", "卧室灯太暗了",
    "儿童房灯光调亮一点", "厨房灯光亮些", "阳台开灯", "玄关照明太暗",
    "卫生间灯光调亮", "主卧亮度调高", "客厅光线亮一点", "书房灯太暗",
    "次卧照明调亮", "儿童房开灯吧",
    "厨房灯调亮点", "阳台照明亮一些", "玄关灯调亮", "卧室灯光亮些", "书房开灯",
]
for text in bright_lines:
    add("A", text, [{"predicate": LIGHT, "value": {"level": "bright"}}])

# ------------------------------------------------------------ B: polarity traps
neg_lines = [
    ("主卧不要太吵，我会睡不好", QUIET, {"level": "quiet"}),
    ("客厅别太吵，孩子在写作业", QUIET, {"level": "quiet"}),
    ("书房我不想听到噪音，安静些", QUIET, {"level": "quiet"}),
    ("次卧睡觉时不要有声音", QUIET, {"level": "quiet"}),
    ("卧室别出声", QUIET, {"level": "quiet"}),
    ("儿童房不要吵到孩子", QUIET, {"level": "quiet"}),
    ("厨房别太吵闹", QUIET, {"level": "quiet"}),
    ("阳台我不喜欢吵闹", QUIET, {"level": "quiet"}),
    ("主卧灯光别太亮，我不喜欢刺眼", LIGHT, {"level": "dim"}),
    ("客厅灯不要太亮", LIGHT, {"level": "dim"}),
    ("书房光线不要刺眼", LIGHT, {"level": "dim"}),
    ("次卧灯光别调太亮", LIGHT, {"level": "dim"}),
    ("卧室灯不要那么亮", LIGHT, {"level": "dim"}),
    ("儿童房灯光别太强", LIGHT, {"level": "dim"}),
    ("厨房灯不需要太亮", LIGHT, {"level": "dim"}),
    ("阳台照明不要太强", LIGHT, {"level": "dim"}),
    ("玄关灯别太亮", LIGHT, {"level": "dim"}),
    ("卫生间光线不要太亮", LIGHT, {"level": "dim"}),
    ("主卧空调不要开到28度，26度就够了", TEMP, {"celsius": 26}),
    ("客厅温度别设30度，25度吧", TEMP, {"celsius": 25}),
]
for text, pred, val in neg_lines:
    add("B", text, [{"predicate": pred, "value": val}], "negation must not flip the fact")

# --------------------------------------------------------------- C: noise only
noise = [
    "今天中午吃什么好呢", "现在几点了", "帮我看看明天的天气", "这个周末有什么安排",
    "我刚才在超市买了鸡蛋", "手机快没电了", "记得提醒我下午开会",
    "你叫什么名字", "今天心情不错", "外面下雨了吗", "快递到了没",
    "我想看个电影", "晚饭做红烧肉吧", "明天要早起", "这个月工资到账了",
    "猫又把杯子碰倒了", "周末想去爬山", "我有点困了", "这首歌真好听", "洗衣机好像坏了",
    "帮我订一张去上海的机票", "楼下超市几点关门", "最近在追一部剧", "今天地铁好挤",
    "记得买牛奶", "孩子在写作业", "我在加班", "车该保养了", "冰箱里还有菜吗", "晚安",
]
for text in noise:
    add("C", text, [], "must extract nothing")

# --------------------------------------------------- D: out-of-vocabulary facts
oov = [
    ("书房网速太慢了", "network_quality", {"level": "slow"}),
    ("客厅窗帘拉上吧", "curtain_state", {"state": "closed"}),
    ("主卧加湿器开到最大", "humidifier_level", {"level": "max"}),
    ("厨房抽油烟机声音很大", "hood_noise", {"level": "high"}),
    ("阳台想养几盆花", "balcony_plan", {"text": "grow flowers"}),
    ("次卧的床垫太软了", "mattress_firmness", {"level": "soft"}),
    ("客厅沙发该换了", "furniture_state", {"text": "replace sofa"}),
    ("书房需要一把新椅子", "furniture_need", {"text": "new chair"}),
    ("玄关的鞋柜太小", "storage_capacity", {"level": "small"}),
    ("卫生间镜子有雾", "mirror_state", {"state": "foggy"}),
    ("儿童房想贴卡通壁纸", "wallpaper_plan", {"text": "cartoon"}),
    ("卧室的窗帘遮光不好", "curtain_quality", {"level": "poor"}),
    ("厨房水龙头漏水", "faucet_state", {"state": "leaking"}),
    ("阳台晾衣架不够用", "drying_rack", {"level": "insufficient"}),
    ("客厅电视声音太闷", "tv_audio", {"level": "muffled"}),
    ("书房插座不够", "socket_count", {"level": "insufficient"}),
    ("主卧衣柜门关不严", "wardrobe_state", {"state": "ajar"}),
    ("次卧空调有异味", "ac_state", {"state": "smelly"}),
    ("卫生间地漏堵了", "drain_state", {"state": "blocked"}),
    ("玄关灯开关松了", "switch_state", {"state": "loose"}),
    ("儿童房需要加个书架", "furniture_need", {"text": "bookshelf"}),
    ("厨房垃圾桶太小", "bin_capacity", {"level": "small"}),
    ("客厅地毯脏了", "carpet_state", {"state": "dirty"}),
    ("书房隔音不好", "soundproofing", {"level": "poor"}),
    ("阳台地面有积水", "balcony_state", {"state": "flooded"}),
    ("卧室门锁有点卡", "door_lock", {"state": "stuck"}),
    ("厨房冰箱太小了", "fridge_capacity", {"level": "small"}),
    ("客厅空调出风口有灰", "ac_state", {"state": "dusty"}),
    ("次卧窗台想放绿植", "windowsill_plan", {"text": "plants"}),
    ("卫生间需要装个置物架", "shelf_need", {"text": "shelf"}),
]
for text, pred, val in oov:
    add("D", text, [{"predicate": pred, "value": val}], "outside the home-zh pack: coverage gap")

# ------------------------------------------------------------------- E: updates
update_pairs = [
    ("主卧空调调到22度", "主卧空调调到25度", "主卧", TEMP, 22, 25),
    ("客厅温度设成24度", "客厅温度改成27度", "客厅", TEMP, 24, 27),
    ("书房暖气22度", "书房暖气改成20度", "书房", TEMP, 22, 20),
    ("次卧保持安静", "次卧不用静音了，正常就好", "次卧", QUIET, {"level": "quiet"}, None),
    ("主卧灯光调暗", "主卧灯光调亮", "主卧", LIGHT, {"level": "dim"}, {"level": "bright"}),
    ("客厅灯亮一点", "客厅灯还是暗一点好", "客厅", LIGHT, {"level": "bright"}, {"level": "dim"}),
    ("儿童房空调26度", "儿童房空调改成24度", "儿童房", TEMP, 26, 24),
    ("厨房灯光调亮", "厨房灯光太亮了，调暗", "厨房", LIGHT, {"level": "bright"}, {"level": "dim"}),
    ("阳台温度定在18度", "阳台温度改成21度", "阳台", TEMP, 18, 21),
    ("卧室暖气23度", "卧室暖气改成25度", "卧室", TEMP, 23, 25),
    ("玄关保持安静", "玄关不用静音", "玄关", QUIET, {"level": "quiet"}, None),
    ("卫生间灯太暗了", "卫生间灯不用那么亮", "卫生间", LIGHT, {"level": "bright"}, {"level": "dim"}),
    ("书房静音模式", "书房正常模式就行", "书房", QUIET, {"level": "quiet"}, None),
    ("主卧温度20度", "主卧温度22度", "主卧", TEMP, 20, 22),
    ("客厅空调26度", "客厅空调24度", "客厅", TEMP, 26, 24),
]
for before, after, subj, pred, v1, v2 in update_pairs:
    add("E", before, [{"predicate": pred, "value": v1 if not isinstance(v1, dict) else v1}],
        f"pair-before subject={subj}")
    add("E", after, [{"predicate": pred, "value": v2}] if v2 is not None else [],
        f"pair-after subject={subj} (retires the previous value)")

assert len(items) == 200, len(items)
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as fh:
    for it in items:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")

from collections import Counter
print(f"wrote {len(items)} items -> {OUT}")
print(Counter(i["kind"] for i in items))
