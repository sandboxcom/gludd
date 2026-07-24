!define APPNAME "gludd"
Name "${APPNAME} ${VERSION}"
OutFile "${BUILDDIR}\gludd-${VERSION}-setup-x86_64.exe"
InstallDir "$PROGRAMFILES\${APPNAME}"
Page directory
Page instfiles

Section ""
  SetOutPath "$INSTDIR"
  File "${BUILDDIR}\windows\gludd.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\gludd.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
