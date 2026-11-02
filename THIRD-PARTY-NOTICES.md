# Third-party notices

pyhttp bundles the following open-source components in its desktop builds.
Each is used under its own license, which permits inclusion in proprietary
software; the license texts are in each project's repository and in the
`site-packages` folder of a source installation.

| Component | License | Project |
|---|---|---|
| Python (runtime) | PSF License | https://www.python.org |
| CodeMirror 6 (editor, bundled in `static/vendor/codemirror.js`), Lezer parsers, style-mod, w3c-keyname, crelt | MIT | https://codemirror.net |
| Inter typeface (default UI font, bundled in `static/vendor/fonts/`) | SIL Open Font License 1.1 | https://github.com/rsms/inter |
| QuickJS (python-quickjs) or V8 via mini-racer (JavaScript scripts) | MIT / BSD-3-Clause | https://github.com/PetterS/quickjs, https://github.com/bpcreech/PyMiniRacer |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Starlette | BSD-3-Clause | https://github.com/encode/starlette |
| Pydantic, pydantic-core | MIT | https://github.com/pydantic/pydantic |
| annotated-types, typing-inspection, annotated-doc | MIT | https://github.com/annotated-types/annotated-types |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| httpx, httpcore | BSD-3-Clause | https://github.com/encode/httpx |
| h11 | MIT | https://github.com/python-hyper/h11 |
| anyio | MIT | https://github.com/agronholm/anyio |
| sniffio | MIT or Apache-2.0 | https://github.com/python-trio/sniffio |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| certifi (Mozilla CA bundle) | MPL-2.0 | https://github.com/certifi/python-certifi |
| click | BSD-3-Clause | https://github.com/pallets/click |
| colorama (Windows) | BSD-3-Clause | https://github.com/tartley/colorama |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| proxy_tools | MIT | https://github.com/jtushman/proxy_tools |
| bottle | MIT | https://bottlepy.org |
| pythonnet, clr_loader (Windows) | MIT | https://github.com/pythonnet/pythonnet |
| cffi, pycparser | MIT-0 / BSD-3-Clause | https://github.com/python-cffi/cffi |
| pyobjc (macOS) | MIT | https://github.com/ronaldoussoren/pyobjc |
| Microsoft Edge WebView2 Runtime (Windows, not bundled) | Microsoft license | https://developer.microsoft.com/microsoft-edge/webview2 |

Build-time only (not shipped inside the app): PyInstaller (GPL-2.0 with the
bootloader exception, which allows proprietary applications), Pillow
(MIT-CMU), pefile (MIT), altgraph (MIT).

certifi is MPL-2.0: the CA bundle file is included unmodified; the MPL
applies to that file only.
