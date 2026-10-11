# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller --noconfirm PES2021_Adboard_Studio.spec
from PyInstaller.utils.hooks import collect_data_files

datas = [("assets/icon.png", "assets")]
datas += collect_data_files("imageio_ffmpeg")  # ffmpeg.exe ikut dibundel

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
              "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PES2021_Adboard_Studio",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
