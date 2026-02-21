# Teaching an AI to Use Tools: Adding Function Calling to a Voice Bot

After building Miss Harper - the voice bot that [answers phone calls](https://github.com/jaeyow/twilio-chatbot/tree/main/inbound), [makes outbound calls](https://github.com/jaeyow/twilio-chatbot/tree/main/outbound), and [measures her own latency](https://github.com/jaeyow/twilio-chatbot/tree/main/latency) - I kept thinking about what she *couldn't* do.

She could have conversations. She could answer questions about elementary school subjects. She could tell stories. But she couldn't *do* anything. If a student asked "What's on the schedule today?", she'd make something up. If they asked "What does photosynthesis mean?", she'd rely entirely on her training data. And if they wanted a summary of the lesson texted to their phone? Not happening.

She was all talk, no action.

What I needed was **function calling** - the ability for the LLM to recognize when it should use a tool and actually call it mid-conversation. Not as a separate step. Not as a post-processing thing. Right there in the middle of a phone call, the AI says "I need to look that word up" and goes and does it.

Turns out, Pipecat makes this almost suspiciously easy. No JSON schemas to write by hand. No complex routing logic. You write a Python function, register it with the LLM, and Pipecat handles the rest. The LLM can now call your function, wait for the result, and incorporate it into its response - all while keeping the conversation flowing naturally.

Here's what it looks like in action. I call Miss Harper and ask her what "metamorphosis" means. She doesn't guess. She actually calls the Dictionary API, gets the real definition, and reads it back to me. Then when I ask for a lesson summary, she sends it via SMS to my phone. Real actions. Real APIs. Real side effects.

[audio recording placeholder]

In this article, I'll show you exactly how I added three different types of tools to Miss Harper:
1. **get_class_schedule** - Returns mock data (no external API)
2. **lookup_word** - Calls an external Dictionary API
3. **send_lesson_summary** - Sends a real SMS via Twilio

By the end, you'll know how to give any voice bot the ability to take actions during a call. Let's get into it.

## What We're Building

The base bot is the same as the [latency bot](https://github.com/jaeyow/twilio-chatbot/tree/main/latency) - same pipeline, same services, same observers. The only difference is that we're registering three tools with the LLM:

```
Phone Call ──> Twilio ──> Miss Harper (Modal)
                           ├── Deepgram STT (nova-3)
                           ├── Groq LLM (llama-3.3-70b) + function calling
                           │   ├── get_class_schedule()  ← returns mock data
                           │   ├── lookup_word()         ← calls Dictionary API
                           │   └── send_lesson_summary() ← sends SMS via Twilio
                           ├── Deepgram TTS (aura-2-theia-en)
                           ├── Silero VAD + Smart Turn v3
                           └── Latency observers
```

Each tool demonstrates a different pattern:

| Tool | Pattern | Use Case |
|------|---------|----------|
| **get_class_schedule** | Mock data lookup | When you have static data or want to demonstrate the concept without external dependencies |
| **lookup_word** | External API call | When you need to fetch real-time data from a third-party service |
| **send_lesson_summary** | Side effect (SMS) | When the tool needs to *do* something - send a message, update a database, trigger an action |

The flow works like this:
1. Student asks a question: "What does photosynthesis mean?"
2. LLM recognizes it should use the `lookup_word` tool
3. Pipecat executes the tool function (calls the Dictionary API)
4. Result flows back to the LLM
5. LLM incorporates the definition into a natural spoken response: "Great question! Photosynthesis is a noun. It's the process by which green plants use sunlight to synthesize nutrients from carbon dioxide and water..."

All of this happens in real time. The student doesn't hear the tool call happening - they just hear Miss Harper give them an accurate, sourced answer.

## How Function Calling Works in Pipecat

Before we write code, let's understand how Pipecat handles function calling.

Most LLM function calling tutorials make you write JSON schemas by hand - defining parameters, types, descriptions, everything. It's tedious and error-prone. Pipecat takes a different approach: **you just write a Python function**, and Pipecat extracts the schema automatically.

Here's what you do:

```python
async def lookup_word(params: FunctionCallParams, word: str):
    """Look up the definition of a word in the dictionary.
    
    Args:
        word: The word to look up
    """
    # Your implementation here
    result = fetch_definition(word)
    await params.result_callback(result)
```

Then you register it:

```python
llm.register_direct_function(lookup_word)
```

That's it. Pipecat looks at:
- The function name (`lookup_word`)
- The docstring ("Look up the definition of a word in the dictionary")
- The function signature (`word: str`)
- The parameter docstrings ("The word to look up")

And it builds the tool schema for you. The LLM sees this tool as:

```json
{
  "name": "lookup_word",
  "description": "Look up the definition of a word in the dictionary.",
  "parameters": {
    "type": "object",
    "properties": {
      "word": {
        "type": "string",
        "description": "The word to look up"
      }
    },
    "required": ["word"]
  }
}
```

You never write that JSON. You just write the Python function with good docstrings.

When the LLM decides it needs to call a tool:
1. It generates a function call with parameters
2. Pipecat intercepts it and executes your function
3. Your function calls `params.result_callback(result)` with the result
4. Pipecat feeds that result back to the LLM
5. The LLM continues generating its response

This all happens seamlessly in the audio pipeline. The user never hears a pause or an interruption. They just hear the bot give them an informed answer.

## Tool #1: Get Class Schedule (Mock Data)

The simplest tool. No external APIs, no side effects. Just return some data.

Here's where I ran into my first gotcha. I originally wrote `get_class_schedule` the same way as the other tools — using `register_direct_function` with a `FunctionCallParams` signature. It looked clean, it looked right, and it completely didn't work.

The problem: Groq sends `arguments=null` for tools that have no parameters. Pipecat's `DirectFunctionWrapper` tries to unpack that as keyword arguments — `**None` — and crashes. There's an open issue about it, but the fix for now is to use the lower-level registration API that calls your handler with positional arguments instead.

```python
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
```

Notice:
- The 6-parameter signature is the older Pipecat calling convention — `result_callback` is passed directly as a positional argument, not through a `params` object
- We pass the schedule as a native Python list, not as `json.dumps(schedule)` — pipecat handles serialization, and double-encoding the result confuses the LLM
- `arguments` might be `None` here (that's the whole Groq issue), but since we ignore it, it doesn't matter

In a real application, you'd fetch this from a database or calendar API. But for demonstration purposes, mock data works perfectly and shows the pattern without external dependencies.

When a student asks "What's next?" the LLM calls this tool, gets the JSON schedule, and says something like: "Next up is Science at 10 AM! We'll be learning about the water cycle. It's going to be really interesting!"

## Tool #2: Look Up Word (External API)

This one calls a real API - the [Free Dictionary API](https://dictionaryapi.dev/) - to get word definitions.

```python
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
```

Key differences from the schedule tool:
- It takes a parameter (`word: str`) - the LLM extracts this from the conversation
- It makes an actual HTTP request using `aiohttp`
- It has error handling (API failures, word not found)
- It formats the result into a readable string

When a student asks "What does metamorphosis mean?", the LLM:
1. Recognizes it should call `lookup_word`
2. Extracts the word: `"metamorphosis"`
3. Calls the function with `word="metamorphosis"`
4. Waits for the API response
5. Gets back: "metamorphosis (noun): the process of transformation from an immature form to an adult form in two or more distinct stages"
6. Incorporates that into a spoken response

The student just hears: "Great question! Metamorphosis is a noun. It means the process of transformation from an immature form to an adult form in two or more distinct stages. A perfect example is how a caterpillar transforms into a butterfly!"

## Tool #3: Send Lesson Summary (Side Effect)

This tool doesn't just return data - it *does* something. It sends a real SMS message via the Twilio REST API.

```python
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
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
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
```

This one is more complex because:
- It needs access to `caller_number`, `account_sid`, `auth_token`, and `twilio_number` (we'll handle this with closures)
- It has multiple failure modes (no phone number, API error, etc.)
- It performs a side effect - it actually sends a message
- The LLM generates the `summary` parameter based on the conversation context

When a student says "Can you send me a summary?", the LLM:
1. Calls `send_lesson_summary` with a summary it generates from the conversation
2. The function sends the SMS
3. Returns confirmation
4. Miss Harper says "I've sent the lesson summary to your phone!"

A few seconds later, the student's phone buzzes with a text message containing a summary of what they talked about.

## Registering the Tools

Tools need to be registered with the LLM before they can be used. But there's a problem: the tools need access to runtime data (like `caller_number` and Twilio credentials), but they're called by the LLM framework, not by our code.

The solution? **Closures**. We define the tools inside a function that captures the data they need:

```python
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
    
    async def get_class_schedule(function_name, tool_call_id, arguments, llm, context, result_callback):
        # Uses the older 6-param style — workaround for Groq sending arguments=null
        # for parameter-free tools, which crashes DirectFunctionWrapper
        pass

    async def lookup_word(params: FunctionCallParams, word: str):
        # Implementation here
        pass

    async def send_lesson_summary(params: FunctionCallParams, summary: str):
        # This can access caller_number, account_sid, etc.
        # because they're captured from the enclosing scope
        pass

    # get_class_schedule uses the older API + a manual schema (Groq workaround)
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
```

Now in [bot.py](https://github.com/jaeyow/twilio-chatbot/blob/main/function-calling/bot.py), we call `register_tools` after creating the LLM:

```python
async def run_bot(
    transport: BaseTransport,
    handle_sigint: bool,
    testing: bool,
    call_sid: str = "",
    caller_number: str = "",
):
    llm = GroqLLMService(api_key=os.getenv("GROQ_API_KEY"))

    # Register function calling tools on the LLM
    tools_schema = register_tools(
        llm,
        caller_number=caller_number,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
    )

    # Pass the tools schema to the LLM context so Groq knows what's available
    context = LLMContext(messages, tools=tools_schema)

    # ... rest of bot setup
```

This pattern keeps the tools clean and testable while giving them access to the data they need.

## Updating the System Prompt

The LLM needs to know it has tools available. We update Miss Harper's system prompt to mention them:

```python
messages = [
    {
        "role": "system",
        "content": (
            "You are Miss Harper, an elementary school teacher in an audio call. "
            "Your output will be converted to audio so don't include special characters in your answers. "
            "You are an expert in answering questions about elementary school subjects like math, science, history, and literature. "
            # ... existing prompt ...
            "\n\n"
            "You have access to the following tools:\n"
            "- You can check today's class schedule when students ask what's next or what subjects are planned for today.\n"
            "- You can look up word definitions when students ask what a word means.\n"
            "- You can send a lesson summary via text message when the student asks for one or when the lesson ends.\n"
            "\n"
            "Use these tools naturally in conversation. When you use a tool, incorporate the results into your spoken response."
        ),
    },
]
```

This tells the LLM:
- What tools are available (though it already knows from the schemas)
- When to use them (high-level guidance)
- How to present the results (incorporate them naturally)

## Testing It Out

Deploy the bot to Modal:

```sh
cd function-calling
cp env.example .env
# Fill in your API keys and Twilio credentials
modal serve modal_app.py
```

Point your Twilio number to the Modal URL, and make a test call. Try:

**Schedule test:**
- You: "What's on the schedule today?"
- Miss Harper: *calls get_class_schedule()* "Let me check! Today we have Math at 9 AM where we'll work on multiplication tables, then Science at 10 AM to learn about the water cycle..."

**Dictionary test:**
- You: "What does metamorphosis mean?"
- Miss Harper: *calls lookup_word("metamorphosis")* "Great question! Metamorphosis is a noun. It means the process of transformation from an immature form to an adult form in two or more distinct stages..."

**SMS test:**
- You: "Can you send me a summary of what we learned?"
- Miss Harper: *calls send_lesson_summary() with a generated summary* "I've sent the lesson summary to your phone!"
- *Your phone buzzes with an SMS from Miss Harper*

The tools are completely transparent to the student. They don't hear anything different. The bot just sounds more knowledgeable and capable.

## What This Unlocks

Adding function calling transforms Miss Harper from a conversational bot into an **agent** - an AI that can take actions in the world.

This pattern scales to any tool you can imagine:

**Information retrieval:**
- Weather API - "What's the weather like today?"
- Database queries - "What's John's current grade in Math?"
- Calendar API - "When is the next parent-teacher conference?"

**Side effects:**
- Send emails - "Email my parents about my homework"
- Update databases - "Mark this assignment as complete"
- Trigger webhooks - "Schedule a makeup class"

**Multi-step workflows:**
- Check inventory *then* place order *then* send confirmation
- Verify student ID *then* pull grades *then* generate report

The key insight is that function calling isn't just about fetching data - it's about giving the LLM the ability to *interact* with systems. It can read and write. It can query and update. It can observe and act.

Miss Harper went from being a smart chatbot to being a teaching assistant that can actually help with administrative tasks. That's the power of function calling.

## Try It Yourself

All the code is in the [`function-calling/` directory](https://github.com/jaeyow/twilio-chatbot/tree/main/function-calling) of the repo. It's built on the same foundation as the latency bot, so if you've been following along, the structure will look familiar.

Deploy it, call it, and watch the logs as the tools get invoked. You'll see entries like:

```
INFO | Tool called: lookup_word(word='photosynthesis')
INFO | Tool called: send_lesson_summary(to='+1234567890')
```

That's the LLM deciding - mid-conversation - that it needs to call a tool. And Pipecat making it happen, seamlessly, without breaking the flow of the call.

What tools would you add? A calculator for math problems? A translation API for language lessons? A quiz generator? The pattern is the same - write a Python function, register it, and the LLM can use it.

That's what I'm exploring next: pushing function calling further - chaining tools together, handling errors gracefully, and figuring out what happens when you give an AI access to more complex actions. But that's a story for another article.
