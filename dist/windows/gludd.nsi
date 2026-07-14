; gludd.nsi — NSIS installer for General Ludd Agent on Windows
; Usage: makensis -DVERSION=0.1.0 -DBUILDDIR=dist dist/windows/gludd.nsi

!define PRODUCT_NAME "General Ludd Agent"
!define PRODUCT_PUBLISHER "sandboxcom"
!define PRODUCT_WEB_SITE "https://github.com/sandboxcom/gludd"
!define PRODUCT_DIR_REGKEY "Software\${PRODUCT_NAME}"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif

SetCompressor lzma

Name "${PRODUCT_NAME}"
OutFile "${BUILDDIR}\gludd-${VERSION}-setup-x86_64.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" "InstallDir"
RequestExecutionLevel admin

Section "MainSection" SEC01
  SetOutPath "$INSTDIR"
  File "${BUILDDIR}\windows\gludd.exe"

  SetOutPath "$INSTDIR\config"
  File /r "${BUILDDIR}\..\config\*.yml"

  SetOutPath "$INSTDIR\templates"
  File /r "${BUILDDIR}\..\templates\*"

  SetOutPath "$INSTDIR\playbooks"
  File /r "${BUILDDIR}\..\playbooks\*"
SectionEnd

Section -Post
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"

  ; Add to system PATH
  EnVar::AddToPath "$INSTDIR"
  Pop $0
SectionEnd

Section Uninstall
  Delete "$INSTDIR\uninstall.exe"
  Delete "$INSTDIR\gludd.exe"
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\templates"
  RMDir /r "$INSTDIR\playbooks"
  RMDir "$INSTDIR"

  EnVar::RemoveFromPath "$INSTDIR"
  Pop $0

  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
SectionEnd
