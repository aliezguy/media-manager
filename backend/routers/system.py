from fastapi import APIRouter, Body
from config.settings import load_config, save_config
import yaml
import os

router = APIRouter()

# category.yaml 路径
_CATEGORY_YAML_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "category.yaml")


def _load_category_config() -> dict:
    """加载 category.yaml 配置文件，返回 { movie: {...}, tv: {...} }。"""
    try:
        with open(_CATEGORY_YAML_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@router.get("/config")
def get_configuration():
    return load_config()


@router.post("/config")
def update_configuration(config: dict = Body(...)):
    return save_config(config)


@router.get("/categories")
def get_categories():
    """返回 category.yaml 中的所有分类名称（合并 movie + tv）。

    Response::

        {
          "movie": ["动画电影", "华语电影", "外语电影"],
          "tv": ["国漫", "日漫", "其他动漫", "纪录片", "儿童", "综艺", "国产剧", "欧美剧", "日韩剧", "未分类"],
          "all": ["国产剧", "综艺", ...]   // 去重合并
        }
    """
    config = _load_category_config()
    movie_cats = list(config.get("movie", {}).keys())
    tv_cats = list(config.get("tv", {}).keys())
    all_cats = list(dict.fromkeys([*tv_cats, *movie_cats]))  # 保持顺序 + 去重
    return {
        "movie": movie_cats,
        "tv": tv_cats,
        "all": all_cats,
    }