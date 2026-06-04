!macro customHeader
  ; Show detail panel in assisted installer / uninstaller.
  ShowInstDetails show
  ShowUninstDetails show
!macroend

!macro customInstall
  ; Re-enable detail output and print extracted files list.
  SetDetailsPrint both
  DetailPrint "Installation finished, listing extracted files under $INSTDIR ..."
  Push "$INSTDIR"
  Call PrintInstalledFilesRecursive
!macroend

Function PrintInstalledFilesRecursive
  Exch $0
  Push $1
  Push $2
  Push $3

  FindFirst $1 $2 "$0\*"
  loop:
    StrCmp $2 "" done
    StrCmp $2 "." next
    StrCmp $2 ".." next

    StrCpy $3 "$0\$2"
    IfFileExists "$3\*.*" 0 is_file
      DetailPrint "[DIR] $3"
      Push "$3"
      Call PrintInstalledFilesRecursive
      Goto next

    is_file:
      DetailPrint "$3"

    next:
      FindNext $1 $2
      Goto loop

  done:
    FindClose $1

    Pop $3
    Pop $2
    Pop $1
    Pop $0
FunctionEnd
