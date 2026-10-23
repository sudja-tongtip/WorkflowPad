"""
story_generator_new.py — entry point
Run: python story_generator.py
"""
import logging
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

import flet as ft
from story_generator.main_ui import main
from story_generator.constants import APP_NAME, APP_VERSION
import app_error

ft.app(app_error.guard(ft, main, APP_NAME, APP_VERSION))   # friendly error dialog instead of Flet's red 'application crashed' page
