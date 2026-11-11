#!/bin/bash
cd /d/AI/MyApps/WorkflowPad
for t in tests/test_*.py; do
  r=$(PYTHONIOENCODING=utf-8 timeout 280 python $t 2>&1 | tail -1 | cut -c1-110)
  echo "$t :: $r"
done
echo FINISHED
