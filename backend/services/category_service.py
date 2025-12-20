import yaml
import os
import logging
from config.settings import DATA_DIR

logger = logging.getLogger("uvicorn")

RULES_FILE = os.path.join(DATA_DIR, 'category.yaml')

def load_rules():
    if not os.path.exists(RULES_FILE):
        logger.warning("⚠️ 未找到分类规则文件 category.yaml")
        return {}
    try:
        with open(RULES_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
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
        
    # 将规则转为列表
    rule_list = [str(x).strip().upper() for x in str(rule_val).split(',')]
    # 将数据转为字符串列表
    data_list = [str(x).strip().upper() for x in data_val]
    
    # 取交集，如果有交集则命中
    return bool(set(rule_list) & set(data_list))

def determine_category(tmdb_info, media_type_cn):
    """
    根据 TMDB 信息和规则，决定分类
    """
    rules = load_rules()
    
    # 确定根节点 (movie 或 tv)
    root_key = "movie" if media_type_cn == "电影" else "tv"
    type_rules = rules.get(root_key, {})
    
    if not type_rules:
        return None

    # 提取 TMDB 关键特征
    # 1. 产地
    origin_countries = tmdb_info.get("origin_country", []) # TV 才有
    if root_key == "movie":
        # 电影通常用 production_countries
        p_countries = tmdb_info.get("production_countries", [])
        origin_countries = [c.get("iso_3166_1") for c in p_countries]
    
    # 2. 类型 ID
    genres = tmdb_info.get("genres", [])
    genre_ids = [g.get("id") for g in genres]
    
    # 3. 原始语言
    original_language = [tmdb_info.get("original_language")]

    # 遍历规则 (按 YAML 里的顺序)
    for category_name, conditions in type_rules.items():
        if conditions is None:
            # 如果条件为空（如"外语电影"、"未分类"），且排在最后，直接命中
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
            logger.info(f"✅ 命中分类规则: [{category_name}]")
            return category_name

    return None