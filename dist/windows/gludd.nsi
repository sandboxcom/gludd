!define PRODUCT_NAME "Gludd"
!define PRODUCT_VERSION "${VERSION}"
!define PRODUCT_PUBLISHER "General Ludd"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "${BUILDDIR}\gludd-${VERSION}-setup-x86_64.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
RequestExecutionLevel admin

Section "Install"
    SetOutPath "$INSTDIR"
    File "${BUILDDIR}\windows\gludd.exe"
    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\gludd.exe"
    Delete "$INSTDIR\uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd
