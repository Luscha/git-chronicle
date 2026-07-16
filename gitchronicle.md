# gitchronicle

## Scope

<!-- The pipeline analyses ONLY paths matching an `include:` glob and no
     `exclude:` glob. Review the drafted verdicts — the evidence for each is
     in the comment. Scope edits re-run analysis for affected commits. -->

- include: Locale/italy/quest/**
- include: Locale/italy/libs/**
- include: Locale/italy/system/**
- exclude: Locale/**    <!-- 20559 files, 43161 touches, 2019-01-03→2026-07-06, exts svn-base,msa,quest,txt (repo-overlap 0.88) -->
- exclude: Locale/data/**    <!-- 18420 files, 30771 touches, 2019-01-03→2026-07-04, exts svn-base,msa,msm,txt (repo-overlap 0.95) -->
- exclude: Extern-Server/Extern/**    <!-- 13345 files, 28199 touches, 2020-07-22→2024-02-01 (gone from worktree), exts hpp,h,cpp,ipp (repo-overlap 0.97) -->
- exclude: dev/locale/**    <!-- 13737 files, 27475 touches, 2017-11-23→2019-01-03 (gone from worktree), exts msa,svn-base,txt,msm (repo-overlap 0.96) -->
- exclude: thirdparty/**    <!-- 20597 files, 22378 touches, 2024-04-13→2026-06-20, exts h,py,cpp,c (repo-overlap 0.63), ecosystem markers -->
- exclude: Locale/italy/**    <!-- 1908 files, 11852 touches, 2019-01-03→2026-07-06, exts quest,txt,lua,forge (repo-overlap 0.73) -->
- exclude: Client-Files/locale/**    <!-- 1123 files, 5665 touches, 2019-01-22→2026-07-04, exts txt,html,py,yaml (repo-overlap 0.54) -->
- exclude: thirdparty/python3/**    <!-- 4905 files, 4905 touches, 2025-12-27→2025-12-27, exts py,h,rst,c (repo-overlap 0.72) -->
- exclude: thirdparty/bgfx/**    <!-- 4005 files, 4009 touches, 2024-04-13→2026-06-20, exts txt,h,cpp,bin (repo-overlap 0.69), ecosystem markers -->
- acknowledge: Web-Sites/CCC/**    <!-- 1858 files, 3721 touches, 2021-09-08→2025-01-22 (gone from worktree), exts js,html,gif,css (repo-overlap 0.02) -->
- exclude: Web-Sites/**    <!-- 1858 files, 3721 touches, 2021-09-08→2025-01-22 (gone from worktree), exts js,html,gif,css (repo-overlap 0.02) -->
- exclude: thirdparty/python/**    <!-- 1704 files, 2607 touches, 2024-04-13→2026-06-20, exts c,h,pyc,py (repo-overlap 0.73) -->
- exclude: thirdparty/cython/**    <!-- 2124 files, 2124 touches, 2024-04-13→2024-05-16, exts pyx,py,pxd,srctree (repo-overlap 0.18), ecosystem markers -->
- exclude: thirdparty/eigen/**    <!-- 1867 files, 1868 touches, 2024-04-13→2026-06-20, exts cpp,h,dox,hh (repo-overlap 0.81) -->
- exclude: ccc/**    <!-- 626 files, 1554 touches, 2025-11-26→2026-07-01, exts ts,vue,sql,json (repo-overlap 0.0), ecosystem markers -->
- exclude: thirdparty/crashpad/**    <!-- 1334 files, 1336 touches, 2024-04-13→2026-06-20, exts cc,h,c,txt (repo-overlap 0.42), ecosystem markers -->
- exclude: thirdparty/cryptopp/**    <!-- 587 files, 1174 touches, 2024-04-13→2024-12-30, exts cpp,h,txt,dat (repo-overlap 0.78) -->
- exclude: thirdparty/rapidxml/**    <!-- 1135 files, 1136 touches, 2024-04-13→2026-06-20, exts xml,cpp,txt,hpp (repo-overlap 0.05), ecosystem markers -->
- exclude: ccc/frontend/**    <!-- 366 files, 819 touches, 2025-11-26→2026-06-30, exts vue,ts,json,conf (repo-overlap 0.0), ecosystem markers -->
- exclude: ccc/backend/**    <!-- 218 files, 631 touches, 2025-11-26→2026-07-01, exts ts,sql,json,example (repo-overlap 0.0), ecosystem markers -->
- exclude: thirdparty/python_27/**    <!-- 545 files, 545 touches, 2025-12-27→2025-12-27, exts c,h,txt,in (repo-overlap 0.96) -->
- exclude: Server/libdevil/**    <!-- 443 files, 450 touches, 2019-01-03→2024-02-01, exts cpp,h,ico,jpg (repo-overlap 0.62), ecosystem markers -->
- exclude: Locale/conf/**    <!-- 195 files, 410 touches, 2026-03-06→2026-07-01, exts yaml (repo-overlap 0.0) -->
- exclude: Tools/UniversalElements-Updater/**    <!-- 165 files, 389 touches, 2019-01-03→2026-05-25, exts png,cs,xaml,csproj (repo-overlap 0.01) -->
- exclude: thirdparty/spdlog/**    <!-- 172 files, 344 touches, 2024-04-13→2024-12-24, exts h,cpp,txt,in (repo-overlap 0.88) -->
- include: dev/**    <!-- 15835 files, 31835 touches, 2016-05-29→2019-01-03 (gone from worktree), exts msa,svn-base,txt,h (repo-overlap 0.94) -->
- include: Extern-Server/**    <!-- 14551 files, 30612 touches, 2020-07-22→2024-04-13 (gone from worktree), exts hpp,h,cpp,txt (repo-overlap 0.95) -->
- include: Client-Files/**    <!-- 5203 files, 21084 touches, 2019-01-22→2026-07-04, exts py,json,txt,pyc (repo-overlap 0.61) -->
- include: Server/**    <!-- 2132 files, 18062 touches, 2019-01-03→2026-07-08, exts cpp,h,c,sh (repo-overlap 0.96), ecosystem markers -->
- include: Client/**    <!-- 2122 files, 15935 touches, 2019-01-03→2026-07-04, exts cpp,h,py,txt (repo-overlap 0.93) -->
- include: Server/game/**    <!-- 649 files, 13639 touches, 2019-01-03→2026-07-08, exts cpp,h,hpp,txt (repo-overlap 1.0) -->
- include: Client-Files/root/**    <!-- 834 files, 7082 touches, 2019-01-22→2026-07-04, exts py,hlsl,pyi,msm (repo-overlap 0.8) -->
- include: Tools/**    <!-- 3272 files, 5849 touches, 2019-01-03→2026-07-04, exts py,cpp,h,dectest (repo-overlap 0.71) -->
- include: Client/UserInterface/**    <!-- 233 files, 4922 touches, 2019-01-03→2026-07-04, exts cpp,h,txt,vcxproj (repo-overlap 0.98) -->
- include: Tools/GrannyShadersUpdater/**    <!-- 1656 files, 3385 touches, 2024-04-13→2026-03-15, exts py,dectest,pyc,txt (repo-overlap 0.8) -->
- include: Client-Files/.devops/**    <!-- 1078 files, 3238 touches, 2024-12-16→2026-04-25, exts json,py,txt,spec (repo-overlap 0.31) -->
- include: Client/GameLibrary/**    <!-- 237 files, 2848 touches, 2019-01-03→2026-06-18, exts cpp,h,txt,hpp (repo-overlap 0.99) -->
- include: Client/CRootLib/**    <!-- 840 files, 2022 touches, 2021-08-12→2024-02-01, exts py,c,pyc,pxd (repo-overlap 0.75) -->
- include: Client-Files/debug_lib/**    <!-- 1035 files, 1983 touches, 2024-05-16→2026-04-13, exts py,pyc,hpp,pyd (repo-overlap 0.49) -->
- include: Client-Files/lib/**    <!-- 901 files, 1848 touches, 2024-02-28→2026-03-18, exts py,pyc,h,hpp (repo-overlap 0.51) -->
- include: dev/Tools/**    <!-- 746 files, 1492 touches, 2016-05-29→2019-01-03 (gone from worktree), exts h,cpp,bmp,c (repo-overlap 0.66) -->
- include: dev/Server/**    <!-- 674 files, 1445 touches, 2016-05-29→2019-01-03 (gone from worktree), exts h,cpp,c,cc (repo-overlap 0.98) -->
- include: Client/EterLibrary/**    <!-- 201 files, 1424 touches, 2019-01-03→2026-06-07, exts cpp,h,txt,vcxproj (repo-overlap 0.98) -->
- include: dev/Client/**    <!-- 677 files, 1420 touches, 2016-05-29→2019-01-03 (gone from worktree), exts cpp,h,vcxproj,filters (repo-overlap 0.92) -->
- include: Server/db/**    <!-- 88 files, 1262 touches, 2019-01-03→2026-07-06, exts cpp,h,txt,hpp (repo-overlap 1.0) -->
- include: Prototypes/**    <!-- 31 files, 1210 touches, 2019-01-19→2026-07-04, exts txt,xml,xlsm,exe (repo-overlap 0.43) -->
- include: Extern-Server/cryptopp-CRYPTOPP_8_4_0/**    <!-- 601 files, 1202 touches, 2023-04-23→2024-02-01 (gone from worktree), exts cpp,h,txt,dat (repo-overlap 0.79) -->
- include: Client-Files/uiscript/**    <!-- 185 files, 1163 touches, 2019-01-22→2026-07-04, exts py (repo-overlap 1.0) -->
- include: Client/RHI/**    <!-- 90 files, 1154 touches, 2025-12-23→2026-05-24, exts h,cpp,txt,md (repo-overlap 0.99) -->
- include: Extern-Server/libdevil-patched-1.8.0/**    <!-- 462 files, 924 touches, 2023-04-23→2024-02-01 (gone from worktree), exts cpp,h,ico,jpg (repo-overlap 0.58) -->
- include: Server/liblua/**    <!-- 389 files, 783 touches, 2019-01-03→2026-01-24, exts c,h,hpp,o (repo-overlap 0.98) -->
- include: Client/SpeedTreeLibrary/**    <!-- 39 files, 618 touches, 2019-01-03→2026-04-06, exts cpp,h,txt,vcxproj (repo-overlap 0.97) -->
- include: Client/PythonLibrary/**    <!-- 24 files, 539 touches, 2019-01-03→2026-06-19, exts cpp,h,hpp,vcxproj (repo-overlap 0.97) -->
- include: Tools/DumpProto/**    <!-- 204 files, 537 touches, 2019-01-03→2024-12-31, exts c,h,cpp,asm (repo-overlap 0.69) -->
- include: core/**    <!-- 46 files, 532 touches, 2023-02-28→2026-06-30, exts hpp,cpp,h,txt (repo-overlap 1.0) -->
- include: Tools/GrannyPreprocessor/**    <!-- 490 files, 500 touches, 2024-04-13→2026-03-24, exts cpp,h,gr2,vcxproj (repo-overlap 0.63) -->
- include: Server/common/**    <!-- 20 files, 469 touches, 2019-01-03→2026-06-02, exts h,in (repo-overlap 1.0) -->
- include: Client/GrannyLibrary/**    <!-- 35 files, 466 touches, 2019-01-03→2026-06-30, exts cpp,h,vcxproj,txt (repo-overlap 0.95) -->
- include: Client/EffectLibrary/**    <!-- 41 files, 445 touches, 2019-01-03→2026-05-27, exts cpp,h,vcxproj,txt (repo-overlap 0.96) -->
- include: core/include/**    <!-- 29 files, 413 touches, 2024-02-01→2026-06-30, exts hpp,h (repo-overlap 1.0) -->

## Charter

Metin2-derived MMO game: C++ game server, C++/Python client, launcher, world editor
tooling. Features are gameplay capabilities and their supporting engine systems.

- Item prototypes, item stats, item bonuses and item attributes are ONE feature: the
  item system. Do not split them.
- Horse riding, horse mechanics and horse skills are ONE feature.
- Skills and Skill Tree are DISTINCT features; never merge them.
- UI windows, panels and screens belong to the feature they serve (a skill tree window
  is part of Skill Tree); only reusable UI building blocks used by many features stand
  alone as UI framework components.
- Raids, dungeons and world bosses are separate features unless the code says otherwise.
