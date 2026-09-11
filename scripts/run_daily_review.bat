@echo off
rem ============================================
rem  DeepCode daily review auto task
rem  0. prepare data (check + fetch missing)
rem  1. generate review -> knowledge-vault/notes/
rem  2. rebuild RAG index (review searchable)
rem ============================================
cd /d "F:\DEEPCODE"

set LOG=scripts\ollama_tools\daily_review_task.log
echo [%date% %time%] ====== DAILY REVIEW START ====== >> %LOG%

echo [%date% %time%] -- step 0: prepare data -- >> %LOG%
python scripts\ollama_tools\prepare_data.py >> %LOG% 2>&1
echo [%date% %time%] prepare exit=%errorlevel% >> %LOG%

echo [%date% %time%] -- step 1: generate review -- >> %LOG%
python scripts\ollama_tools\daily_review.py --no-prepare >> %LOG% 2>&1
echo [%date% %time%] review exit=%errorlevel% >> %LOG%

echo [%date% %time%] -- step 2: rebuild RAG index -- >> %LOG%
python scripts\ollama_tools\rag_ask.py --rebuild >> %LOG% 2>&1
echo [%date% %time%] rag_rebuild exit=%errorlevel% >> %LOG%

echo [%date% %time%] ====== DAILY REVIEW END ====== >> %LOG%
