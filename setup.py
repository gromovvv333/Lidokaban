"""
Сборка приложения:
    python setup.py py2app
Готовый .app появится в dist/Парсер лидов.app
"""

from setuptools import setup

APP = ["app.py"]
DATA_FILES = ["ui.html"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/icon.icns",
    "plist": {
        "CFBundleName": "Парсер лидов",
        "CFBundleDisplayName": "Парсер лидов",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIdentifier": "local.leadparser.app",
    },
    "packages": ["webview", "phonenumbers"],
    "excludes": ["playwright._impl.__pyinstaller"],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
