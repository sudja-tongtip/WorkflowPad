"""
story_generator_new.py — entry point
Run: python story_generator.py
"""
import logging
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

import flet as ft
from story_generator.main_ui import main

ft.app(main)           
