; gludd.nsi - NSIS installer for gludd (Windows x86_64)
;
; CI contract (.github/workflows/build.yml, "Build NSIS installer" step):
;   choco install nsis -y --no-progress            ; NSIS 3.x
;   Copy-Item dist/gludd.exe dist/windows/gludd.exe ; File source (below)
;   makensis -DVERSION="$env:VERSION" -DBUILDDIR="dist" dist/windows/gludd.nsi
;   certutil -hashfile "dist/gludd-$env:VERSION-setup-x86_64.exe" SHA256 > ...
;
; Both VERSION and BUILDDIR are passed on the makensis command line via -D
; (equivalent to NSIS's /D; NSIS 3.x accepts both prefixes). If either is
; missing the !ifndef guards below halt compilation with a clear message
; instead of silently emitting a mis-named OutFile (e.g. gludd--setup-x86_64.exe)
; that would then break the downstream certutil/artifact-upload steps.
;
; VERSION_PLACEHOLDER is the canonical sed-substitution marker shared with
; dist/debian/control and dist/rpm/gludd.spec; NSIS instead receives VERSION
; via -D on the makensis command line (see CI contract above).

Unicode true
ManifestDPIAware true

; --- Required command-line defines ---------------------------------------
; Fail loudly if the CI step forgot the -D flags OR PowerShell mangled the
; '-' prefix. An undefined VERSION must NEVER produce a silent wrong output.
!ifndef VERSION
  !error "VERSION not defined. Pass -DVERSION=<x.y.z> on the makensis command line."
!endif
!ifndef BUILDDIR
  !error "BUILDDIR not defined. Pass -DBUILDDIR=<dir> on the makensis command line."
!endif

!define APPNAME      "gludd"
!define COMPANYNAME  "sandboxcom"
!define UNINST_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"

; BUILDDIR is ".." (relative to this script at dist/windows/) -> OutFile "../gludd-<ver>-setup-x86_64.exe"
; resolves to dist/gludd-<ver>-setup-x86_64.exe — matching what the CI's
; certutil/artifact steps expect. NSIS resolves OutFile relative to the
; SCRIPT file location, not the CWD.
Name "${APPNAME} ${VERSION}"
OutFile "${BUILDDIR}\gludd-${VERSION}-setup-x86_64.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
RequestExecutionLevel admin

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  ; Source path must match the CI Copy-Item destination (dist/windows/gludd.exe).
  ; The .nsi is also at dist/windows/ so File resolves relative to this directory.
  File "gludd.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName"     "${APPNAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion"  "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher"       "${COMPANYNAME}"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\gludd.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
SectionEnd
