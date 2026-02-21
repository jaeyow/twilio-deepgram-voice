"""Function calling tools for Miss Harper's voice bot.

Three tools demonstrating different patterns:
  1. get_class_schedule — mock data lookup (no external API)
  2. lookup_word         — external API call (Free Dictionary API)
  3. send_lesson_summary — real side effect (Twilio SMS)
"""

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams


def register_tools(
    llm,
    caller_number: str,
    account_sid: str,
    auth_token: str,
    twilio_number: str,
):
    """Register all tools on the LLM service.

    Uses closures to capture caller_number and Twilio credentials
    so tool functions are self-contained without global state.
    """

    # Use the deprecated 6-parameter style for get_class_schedule.
    # Groq sends arguments=null for parameter-free tools; pipecat's
    # DirectFunctionWrapper does **args which crashes on None. The deprecated
    # 6-param style calls the handler with positional args — no **args involved.
    async def get_class_schedule(function_name, tool_call_id, arguments, llm, context, result_callback):
        logger.info("Tool called: get_class_schedule")
        schedule = [
            {"time": "9:00 AM", "subject": "Math", "topic": "Multiplication tables"},
            {"time": "10:00 AM", "subject": "Science", "topic": "The water cycle"},
            {"time": "11:00 AM", "subject": "Reading", "topic": "Charlotte's Web chapter 5"},
            {"time": "12:00 PM", "subject": "Lunch break"},
            {"time": "1:00 PM", "subject": "History", "topic": "Ancient Egypt"},
            {"time": "2:00 PM", "subject": "Art", "topic": "Watercolor painting"},
        ]
        await result_callback(schedule)

    async def lookup_word(params: FunctionCallParams, word: str):
        """Look up the definition of a word in the dictionary.

        Args:
            word: The word to look up
        """
        logger.info(f"Tool called: lookup_word(word={word!r})")
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        meaning = data[0]["meanings"][0]
                        definition = meaning["definitions"][0]["definition"]
                        part_of_speech = meaning["partOfSpeech"]
                        result = f"{word} ({part_of_speech}): {definition}"
                    else:
                        result = f"Sorry, I couldn't find the word '{word}' in the dictionary."
        except Exception as e:
            logger.error(f"Dictionary API error: {e}")
            result = f"Sorry, there was an error looking up the word '{word}'."
        await params.result_callback(result)

    async def send_lesson_summary(params: FunctionCallParams, summary: str):
        """Send a text message with a lesson summary to the student's phone.

        Args:
            summary: A brief summary of what was covered in the lesson
        """
        logger.info(f"Tool called: send_lesson_summary(to={caller_number!r})")

        if not caller_number:
            await params.result_callback(
                "I don't have a phone number to send the summary to."
            )
            return

        if not twilio_number:
            await params.result_callback(
                "SMS is not configured. Ask your teacher to set up the phone number."
            )
            return

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        )
        auth = aiohttp.BasicAuth(account_sid, auth_token)
        data = {
            "From": twilio_number,
            "To": caller_number,
            "Body": f"Lesson Summary from Miss Harper:\n\n{summary}",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, auth=auth, data=data) as resp:
                    if resp.status == 201:
                        resp_data = await resp.json()
                        logger.info(f"SMS sent: SID={resp_data.get('sid')}")
                        await params.result_callback(
                            "I've sent the lesson summary to your phone!"
                        )
                    else:
                        error_text = await resp.text()
                        logger.error(f"Twilio SMS error ({resp.status}): {error_text}")
                        await params.result_callback(
                            "Sorry, I wasn't able to send the text message."
                        )
        except Exception as e:
            logger.error(f"Error sending SMS: {e}")
            await params.result_callback(
                "Sorry, there was an error sending the text message."
            )

    llm.register_function("get_class_schedule", get_class_schedule)
    llm.register_direct_function(lookup_word)
    llm.register_direct_function(send_lesson_summary)

    logger.info(
        f"Registered 3 tools on LLM (caller: {caller_number or 'unknown'})"
    )

    get_class_schedule_schema = FunctionSchema(
        name="get_class_schedule",
        description=(
            "Get today's class schedule with subjects and times. "
            "Call this when a student asks about the schedule, what's next, "
            "or what subjects are planned for today."
        ),
        properties={},
        required=[],
    )
    return ToolsSchema(standard_tools=[get_class_schedule_schema, lookup_word, send_lesson_summary])
