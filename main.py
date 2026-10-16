"""
AI Phone Agent — Twilio + Claude + PostgreSQL
Принимает звонки, ведёт диалог и сохраняет данные в БД.
"""

from fastapi import FastAPI, Request, Form
from fastapi.responses import Response, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
import uvicorn
import os
from dotenv import load_dotenv

from agent import ConversationAgent
from database import Database

load_dotenv()

app = FastAPI(title="AI Phone Agent")
db = Database()

# Хранилище активных разговоров (CallSid → AgentInstance)
active_calls: dict[str, ConversationAgent] = {}


@app.on_event("startup")
async def startup():
    await db.connect()
    await db.create_tables()
    print("✅ База данных подключена")


@app.on_event("shutdown")
async def shutdown():
    await db.disconnect()


@app.post("/call/incoming")
async def incoming_call(
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(default=""),
):
    """Twilio звонит сюда при входящем звонке."""
    agent = ConversationAgent(call_sid=CallSid, caller_phone=From)
    active_calls[CallSid] = agent

    twiml = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/call/gather",
        method="POST",
        language="ru-RU",
        speechTimeout="auto",
        timeout=6,
        enhanced=True,
    )
    gather.say(agent.greeting(), language="ru-RU", voice="alice")
    twiml.append(gather)
    # Если тишина — повторный запрос
    twiml.redirect("/call/incoming", method="POST")

    return Response(content=str(twiml), media_type="text/xml")


@app.post("/call/gather")
async def gather(
    CallSid: str = Form(...),
    From: str = Form(...),
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default="0"),
):
    """Twilio отправляет сюда распознанную речь."""
    agent = active_calls.get(CallSid)
    if not agent:
        # Восстанавливаем агент если сервер перезапустился
        agent = ConversationAgent(call_sid=CallSid, caller_phone=From)
        active_calls[CallSid] = agent

    speech = SpeechResult.strip()

    # Если ничего не распознано — переспросить
    if not speech:
        twiml = VoiceResponse()
        gather = Gather(
            input="speech",
            action="/call/gather",
            method="POST",
            language="ru-RU",
            speechTimeout="auto",
            timeout=6,
        )
        gather.say("Извините, я не расслышал. Повторите, пожалуйста.", language="ru-RU", voice="alice")
        twiml.append(gather)
        twiml.hangup()
        return Response(content=str(twiml), media_type="text/xml")

    reply_text, is_done = await agent.process(speech)

    twiml = VoiceResponse()

    if is_done:
        twiml.say(reply_text, language="ru-RU", voice="alice")
        twiml.hangup()
        # Сохраняем в БД
        data = agent.get_data()
        await db.save_call(data)
        active_calls.pop(CallSid, None)
    else:
        gather = Gather(
            input="speech",
            action="/call/gather",
            method="POST",
            language="ru-RU",
            speechTimeout="auto",
            timeout=6,
        )
        gather.say(reply_text, language="ru-RU", voice="alice")
        twiml.append(gather)
        twiml.say("Звонок завершён. До свидания!", language="ru-RU", voice="alice")
        twiml.hangup()

    return Response(content=str(twiml), media_type="text/xml")


@app.post("/call/status")
async def call_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
):
    """Twilio уведомляет о завершении звонка."""
    if CallStatus in ("completed", "failed", "busy", "no-answer"):
        agent = active_calls.pop(CallSid, None)
        if agent and not agent.is_saved:
            data = agent.get_data()
            if data.get("caller_phone"):
                await db.save_call(data)
    return Response(content="", status_code=204)


# ── REST endpoints для просмотра данных ────────────────────────────────────

@app.get("/calls")
async def list_calls(limit: int = 50):
    """Последние N звонков из базы."""
    return await db.get_calls(limit=limit)


@app.get("/calls/{call_id}")
async def get_call(call_id: int):
    row = await db.get_call_by_id(call_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return row


@app.get("/health")
async def health():
    return {"status": "ok", "active_calls": len(active_calls)}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("DEBUG", "false").lower() == "true",
    )
