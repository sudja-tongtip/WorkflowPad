#!/bin/bash
powershell -c "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ?{\$_.CommandLine -like '*run_web3*' -or \$_.CommandLine -like '*run_web2*'} | %{Stop-Process -Id \$_.ProcessId -Force}"
sleep 2
cd /d/AI/MyApps/WorkflowPad/docs/song_critic_v2 && OUTDATED="${OUTDATED:-0}" SIDE_W="$1" RAW="${2:-TestSong04_gemma_candidate}" PYTHONIOENCODING=utf-8 nohup python run_web3.py > web3.log 2>&1 &
sleep 25
tail -2 /d/AI/MyApps/WorkflowPad/docs/song_critic_v2/web3.log
