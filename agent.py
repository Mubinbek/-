"""
Conversation logic: Claude управляет диалогом и извлекает данные.
"""

import json
import re
from datetime import datetime
import anthropic
import os

SYSTEM_PROMPT = """Ты — голосовой ИИ-помощник. Ведёшь телефонный разговор на русском языке.

ЦЕЛИ:
1. Узнать имя звонящего.
2. Выяснить цель звонка: «appointment» (запись на приём/встречу), «order» (заказ/заявка) или «question» (вопрос).
3. Собрать детали:
   - Для appointment: желаемые дату, время и услугу/специалиста.
   - Для order: что хочет заказать, количество, адрес если нужно.
   - Для question: суть вопроса (ответь если можешь, иначе передай в компанию).
4. Подтвердить данные и попрощаться.

ПРАВИЛА ОБЩЕНИЯ:
- Одно короткое предложение — один вопрос. Не задавай несколько вопросов сразу.
- Будь вежливым, конкретным, не лирическим.
- Говори коротко: максимум 2 предложения за ход.
- Не повторяй то, что уже сказал.

ЗАВЕРШЕНИЕ:
Когда собрал имя + цель + детали, скажи короткое подтверждение и затем на ОТДЕЛЬНОЙ строке выведи только:
DONE:{"name":"...","purpose":"appointment|order|question","details":"...","appointment_datetime":"...","summary":"..."}

Поля JSON:
- name: имя клиента (пусто если не назвал)
- purpose: тип звонка
- details: краткое описание запроса одной фразой
- appointment_datetime: дата и время если есть (иначе пустая строка)
- summary: 1-2 предложения что хотел клиент

Если клиент хочет завершить звонок или говорит «до свидания» — тоже выведи DONE.
"""


class ConversationAgent:
    def __init__(self, call_sid: str, caller_phone: str):
        self.call_sid = call_sid
        self.caller_phone = caller_phone
        self.messages: list[dict] = []
        self.extracted: dict = {}
        self.is_saved = False
        self._turn = 0
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def greeting(self) -> str:
        return (
            "Здравствуйте! Я виртуальный помощник. "
            "Как вас зовут и чем могу помочь?"
        )

    async def process(self, user_text: str) -> tuple[str, bool]:
        """
        Обрабатывает реплику пользователя.
        Возвращает (текст_ответа, разговор_завершён).
        """
        self._turn += 1
        self.messages.append({"role": "user", "content": user_text})

        # Жёсткий лимит: 12 ходов — завершаем
        if self._turn >= 12:
            self._build_extracted(user_text)
            return "Спасибо за звонок! Мы всё зафиксировали. До свидания!", True

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=self.messages,
        )

        raw = response.content[0].text.strip()
        self.messages.append({"role": "assistant", "content": raw})

        # Ищем маркер завершения
        done_match = re.search(r"DONE:(\{.*\})", raw, re.DOTALL)
        if done_match:
            try:
                data = json.loads(done_match.group(1))
                self._fill_extracted(data)
            except json.JSONDecodeError:
                self._build_extracted(user_text)

            # Выдаём часть до DONE как голосовую фразу
            voice_part = raw[: done_match.start()].strip()
            if not voice_part:
                voice_part = "Спасибо! Всё зафиксировано."
            return voice_part, True

        return raw, False

    def _fill_extracted(self, data: dict) -> None:
        self.extracted = {
            "call_sid": self.call_sid,
            "caller_phone": self.caller_phone,
            "caller_name": data.get("name", ""),
            "purpose": data.get("purpose", "question"),
            "details": data.get("details", ""),
            "appointment_datetime": data.get("appointment_datetime", ""),
            "summary": data.get("summary", ""),
            "full_transcript": self._transcript(),
            "called_at": datetime.utcnow(),
        }

    def _build_extracted(self, last_user_text: str) -> None:
        """Fallback: строим данные из истории без JSON."""
        self.extracted = {
            "call_sid": self.call_sid,
            "caller_phone": self.caller_phone,
            "caller_name": "",
            "purpose": "question",
            "details": last_user_text[:200],
            "appointment_datetime": "",
            "summary": f"Звонок завершён на ходу {self._turn}.",
            "full_transcript": self._transcript(),
            "called_at": datetime.utcnow(),
        }

    def _transcript(self) -> str:
        lines = []
        for msg in self.messages:
            role = "Клиент" if msg["role"] == "user" else "Агент"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def get_data(self) -> dict:
        if not self.extracted:
            self._build_extracted("")
        return self.extracted
