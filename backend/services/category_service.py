import yaml
import os
import logging
# 如果你的 config.settings 没问题，保留这个引入；
# 如果报错找不到 DATA_DIR，可以用注释掉的那行 os.path 替代
from config.settings import DATA_DIR

logger = logging.getLogger("uvicorn")

# 🔥 获取当前脚本所在的绝对目录 (backend/services)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 🔥 回退一层找到 backend 目录
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
# 🔥 拼接出准确的 data 路径 (/app/backend/data/category.yaml)
RULES_FILE = os.path.join(BACKEND_DIR, 'data', 'category.yaml')

def load_rules():
    """
    加载规则文件，增加空文件保护
    """
    if not os.path.exists(RULES_FILE):
        # 找不到文件时不报错，只返回空，避免刷屏
        return {}
    try:
        with open(RULES_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            # 🔥 核心修复 1：如果不检查，空文件 safe_load 会返回 None，导致后续报错
            # 这里强制保证返回的是一个字典
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"❌ 规则文件解析失败: {e}")
        return {}

def check_condition(rule_val, data_val):
    """
    通用匹配逻辑：只要规则列表中的任意一项，存在于数据列表中，即命中
    rule_val: 规则字符串 (如 'CN,TW')
    data_val: 实际数据列表 (如 ['CN', 'US'] 或 [16, 35])
    """
    if not rule_val: 
        return True # 规则为空则视为通过
    
    if not data_val:
        return False # 规则不为空，但数据为空，视为不通过
        
    # 将规则转为列表 (字符串转大写，去空格)
    rule_list = [str(x).strip().upper() for x in str(rule_val).split(',')]
    
    # 将数据转为字符串列表 (兼容数字ID和字符串)
    data_list = [str(x).strip().upper() for x in data_val]
    
    # 取交集，如果有交集则命中
    return bool(set(rule_list) & set(data_list))

def determine_category(tmdb_info, media_type_cn):
    """
    根据 TMDB 信息和规则，决定分类
    :param tmdb_info: TMDB 返回的详情字典
    :param media_type_cn: '电影' 或 '电视剧' (或其他)
    """
    # 1. 加载规则 (现在很安全，一定返回字典)
    rules = load_rules()
    if not rules:
        return None
    
    # 2. 确定根节点 (movie 或 tv)
    # 兼容 '电影' / 'movie' 两种写法，防止传参不一致
    is_movie = str(media_type_cn) == "电影" or str(media_type_cn).lower() == "movie"
    root_key = "movie" if is_movie else "tv"
    
    # 🔥 核心修复 2：如果 yaml 里写了 'movie:' 但下面没缩进内容，get 返回 None
    # 使用 ( ... or {} ) 强制转为字典，防止后续 .items() 报错
    type_rules = rules.get(root_key) or {}
    
    if not type_rules:
        return None

    # 3. 提取 TMDB 关键特征 (保留了你优秀的处理逻辑)
    # --- 产地 ---
    origin_countries = tmdb_info.get("origin_country", []) # 默认取 TV 的字段
    if root_key == "movie":
        # 电影通常用 production_countries，结构是 list[dict]
        p_countries = tmdb_info.get("production_countries", [])
        origin_countries = [c.get("iso_3166_1") for c in p_countries if c.get("iso_3166_1")]
    
    # --- 类型 ID ---
    genres = tmdb_info.get("genres", [])
    genre_ids = [g.get("id") for g in genres if g.get("id")]
    
    # --- 原始语言 ---
    # 放入列表是为了配合 check_condition 的 list 交集逻辑
    original_language = [tmdb_info.get("original_language")]

    # 4. 遍历规则
    for category_name, conditions in type_rules.items():
        # 如果条件为 None (yaml里 key 后没写内容)，且不是排在最后的兜底，通常跳过
        # 但如果你想表达 "只要写了这个分类名就直接命中"，可以保留 return
        if not conditions:
            # 这是一个策略选择：如果是空条件，是否直接命中？
            # 建议：如果只想让它作为兜底（比如 "未分类"），可以放在 yaml 最后
            logger.info(f"⚠️ 分类 [{category_name}] 没有定义条件，直接命中")
            return category_name
            
        is_match = True
        
        # 检查 origin_country
        if "origin_country" in conditions:
            if not check_condition(conditions["origin_country"], origin_countries):
                is_match = False
        
        # 检查 genre_ids
        if is_match and "genre_ids" in conditions:
            if not check_condition(conditions["genre_ids"], genre_ids):
                is_match = False
                
        # 检查 original_language
        if is_match and "original_language" in conditions:
             if not check_condition(conditions["original_language"], original_language):
                is_match = False

        if is_match:
            logger.info(f"✅ 命中分类规则: [{category_name}] | 媒体: {tmdb_info.get('title') or tmdb_info.get('name')}")
            return category_name

    return None