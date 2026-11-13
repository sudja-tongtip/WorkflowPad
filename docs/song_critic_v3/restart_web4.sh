#!/bin/bash
# usage: restart_web4.sh <side_w> [profile_dir]
powershell -c "Get-NetTCPConnection -LocalPort 8571 -ErrorAction SilentlyContinue | %{ Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }"
sleep 2
cd /d/AI/MyApps/WorkflowPad/docs/song_critic_v3 && SIDE_W="$1" PROFILE="$2" PYTHONIOENCODING=utf-8 nohup python run_web4.py > web4.log 2>&1 &
sleep 25
tail -3 /d/AI/MyApps/WorkflowPad/docs/song_critic_v3/web4.log
