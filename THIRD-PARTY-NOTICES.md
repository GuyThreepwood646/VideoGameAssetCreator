# Third-party notices

Every package that ships with Video Game Asset Creator, with its licence, read from the installed Python packages in backend/.venv and from the lock files of frontend/ and desktop/. Names and licence identifiers only; each package carries its own full licence text (in its folder or on its project page). Keep this file with the app when you share it. This is information, not legal advice.

## Python packages (backend)

| Package | Version | Licence |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| coacd | 1.0.14 | MIT |
| colorama | 0.4.6 | BSD License |
| fastapi | 0.162.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| pillow | 13.0.0 | MIT-CMU |
| psutil | 7.2.2 | BSD-3-Clause |
| pydantic | 2.14.2 | MIT |
| pydantic_core | 2.50.2 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| SQLAlchemy | 2.1.8 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| trimesh | 5.2.0 | MIT License |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.4 | BSD-3-Clause |

## JavaScript packages in the frontend build

| Package | Version | Licence |
|---|---|---|
| @dimforge/rapier3d-compat | 0.12.0 | Apache-2.0 |
| @tweenjs/tween.js | 23.1.3 | MIT |
| cookie | 1.1.1 | MIT |
| fflate | 0.8.3 | MIT |
| meshoptimizer | 1.1.1 | MIT |
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| react-router | 7.18.5 | MIT |
| scheduler | 0.28.0 | MIT |
| set-cookie-parser | 2.7.3 | MIT |
| three | 0.187.0 | MIT |

## Desktop launcher

The launcher packs Electron, which ships Chromium and Node.js; Electron's own licence files (LICENSE and LICENSES.chromium.html) travel inside every build it makes. Electron is MIT. Nothing else in desktop/ is shipped.

## Used to build or test, not shipped

Python (tests only)

| Package | Version | Licence |
|---|---|---|
| iniconfig | 2.3.1 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.2.0 | MIT |

Frontend build tools and type definitions

| Package | Version | Licence |
|---|---|---|
| @oxc-project/types | 0.156.0 | MIT |
| @oxlint/binding-android-arm-eabi | 1.91.0 | MIT |
| @oxlint/binding-android-arm64 | 1.91.0 | MIT |
| @oxlint/binding-darwin-arm64 | 1.91.0 | MIT |
| @oxlint/binding-darwin-x64 | 1.91.0 | MIT |
| @oxlint/binding-freebsd-x64 | 1.91.0 | MIT |
| @oxlint/binding-linux-arm-gnueabihf | 1.91.0 | MIT |
| @oxlint/binding-linux-arm-musleabihf | 1.91.0 | MIT |
| @oxlint/binding-linux-arm64-gnu | 1.91.0 | MIT |
| @oxlint/binding-linux-arm64-musl | 1.91.0 | MIT |
| @oxlint/binding-linux-ppc64-gnu | 1.91.0 | MIT |
| @oxlint/binding-linux-riscv64-gnu | 1.91.0 | MIT |
| @oxlint/binding-linux-riscv64-musl | 1.91.0 | MIT |
| @oxlint/binding-linux-s390x-gnu | 1.91.0 | MIT |
| @oxlint/binding-linux-x64-gnu | 1.91.0 | MIT |
| @oxlint/binding-linux-x64-musl | 1.91.0 | MIT |
| @oxlint/binding-openharmony-arm64 | 1.91.0 | MIT |
| @oxlint/binding-win32-arm64-msvc | 1.91.0 | MIT |
| @oxlint/binding-win32-ia32-msvc | 1.91.0 | MIT |
| @oxlint/binding-win32-x64-msvc | 1.91.0 | MIT |
| @rolldown/binding-android-arm-eabi | 1.2.16 | MIT |
| @rolldown/binding-android-arm64 | 1.2.16 | MIT |
| @rolldown/binding-darwin-arm64 | 1.2.16 | MIT |
| @rolldown/binding-darwin-x64 | 1.2.16 | MIT |
| @rolldown/binding-freebsd-x64 | 1.2.16 | MIT |
| @rolldown/binding-linux-arm-gnueabihf | 1.2.16 | MIT |
| @rolldown/binding-linux-arm64-gnu | 1.2.16 | MIT |
| @rolldown/binding-linux-arm64-musl | 1.2.16 | MIT |
| @rolldown/binding-linux-ppc64-gnu | 1.2.16 | MIT |
| @rolldown/binding-linux-s390x-gnu | 1.2.16 | MIT |
| @rolldown/binding-linux-x64-gnu | 1.2.16 | MIT |
| @rolldown/binding-linux-x64-musl | 1.2.16 | MIT |
| @rolldown/binding-openharmony-arm64 | 1.2.16 | MIT |
| @rolldown/binding-win32-arm64-msvc | 1.2.16 | MIT |
| @rolldown/binding-win32-x64-msvc | 1.2.16 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @types/node | 24.19.2 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @types/stats.js | 0.17.4 | MIT |
| @types/three | 0.187.0 | MIT |
| @types/webxr | 0.5.24 | MIT |
| @typescript/typescript-aix-ppc64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-darwin-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-freebsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-freebsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-arm | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-loong64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-mips64el | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-ppc64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-riscv64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-s390x | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-netbsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-netbsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-openbsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-openbsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-sunos-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-win32-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-win32-x64 | 7.0.2 | Apache-2.0 |
| @vitejs/plugin-react | 6.2.1 | MIT |
| csstype | 3.2.3 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| fdir | 6.5.0 | MIT |
| fsevents | 2.3.3 | MIT |
| lightningcss | 1.33.0 | MPL-2.0 |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 |
| nanoid | 3.3.20 | MIT |
| oxlint | 1.91.0 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| postcss | 8.5.32 | MIT |
| rolldown | 1.2.16 | MIT |
| source-map-js | 1.2.2 | BSD-3-Clause |
| tinyglobby | 0.2.17 | MIT |
| typescript | 7.0.2 | Apache-2.0 |
| undici-types | 7.24.6 | MIT |
| vite | 8.3.5 | MIT |

Desktop build tools (electron-builder and what it pulls in): 283 packages, listed in desktop/package-lock.json.

Generated by tools/make_credits.py. Do not edit by hand.
