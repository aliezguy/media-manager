from fastapi import APIRouter, Body
from config.settings import load_config, save_config

router = APIRouter()

@router.get("/config")
def get_configuration():
    return load_config()

@router.post("/config")
def update_configuration(config: dict = Body(...)):
    return save_config(config)