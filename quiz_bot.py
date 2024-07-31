import os
import logging
import random
from threading import Timer
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Define your quiz questions and answers
QUESTION_POOL = [
    {"question": "What is the capital of France?", "answer": "Paris", "clue": "It's also known as the City of Lights."},
    {"question": "What is 2 + 2?", "answer": "4", "clue": "It's the sum of two even numbers."},
    {"question": "What is the color of the sky on a clear day?", "answer": "Blue", "clue": "It's the color of the ocean on a sunny day."}
]

# Track user scores and current question indices per chat
user_data = {}
top_user = {"chat_id": None, "id": None, "score": 0}

# List of admin user IDs (populate with actual admin IDs)
ADMINS = [int(id) for id in os.getenv('ADMINS', '').split(',') if id.isdigit()]

# Constants
MIN_POINTS = 1
MAX_POINTS = 10
POINTS_DECREASE_INTERVAL = 5  # seconds
QUESTION_TIMER_DURATION = 30  # seconds

# Global application reference
application = None

# Function to handle points decrease with fixed value
def decrease_points(user_id, chat_id):
    user = user_data[chat_id].get(user_id)
    if user and 'score' in user:
        # Decrease points based on a fixed value
        user['score'] = max(0, user['score'] - MIN_POINTS)
        logger.info(f"User {user_id} in chat {chat_id} has new score: {user['score']}")
        
        # Schedule the next decrease
        Timer(POINTS_DECREASE_INTERVAL, decrease_points, args=[user_id, chat_id]).start()

# Function to handle question timeout
def question_timeout(user_id, chat_id, context):
    user = user_data[chat_id].get(user_id)
    if user and 'current_question' in user:
        current_question_index = user['current_question']
        if current_question_index < len(user['questions']):
            correct_answer = user['questions'][current_question_index]['answer']
            user['incorrect_attempts'] = 0  # Reset incorrect attempts
            user['current_question'] += 1  # Move to the next question
            if user['current_question'] >= len(user['questions']):
                user['current_question'] = len(user['questions']) - 1  # Ensure index is valid
                logger.info(f"User {user_id} in chat {chat_id} has finished the quiz with score: {user['score']}")
                # Automatically stop the bot after quiz
                application.stop()
            next_question = user['questions'][user['current_question']]['question']
            Timer(POINTS_DECREASE_INTERVAL, decrease_points, args=[user_id, chat_id]).start()
            # Notify the user that their time has run out
            context.bot.send_message(chat_id=chat_id, text=f'Time\'s up! The correct answer was "{correct_answer}".\nNext question: {next_question}')

            # Delete the previous question message
            if user['question_message_id']:
                context.bot.delete_message(chat_id=chat_id, message_id=user['question_message_id'])

async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Initialize user data for the chat
    if chat_id not in user_data:
        user_data[chat_id] = {}
        
    # Initialize user data
    if user_id not in user_data[chat_id]:
        shuffled_questions = QUESTION_POOL.copy()
        random.shuffle(shuffled_questions)
        
        user_data[chat_id][user_id] = {
            "score": 0,
            "current_question": 0,
            "questions": shuffled_questions,
            "timer": None,
            "incorrect_attempts": 0,
            "question_timer": None,
            "question_message_id": None
        }
    
    await update.message.reply_text('Welcome to the quiz bot! Type /quiz to start the quiz.')

async def quiz(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if chat_id not in user_data or user_id not in user_data[chat_id]:
        await update.message.reply_text('Please start by typing /start')
        return
    
    user = user_data[chat_id][user_id]
    current_question_index = user['current_question']
    
    if current_question_index >= len(user['questions']):
        await update.message.reply_text('Quiz completed! Your final score is {}.'.format(user['score']))
        return
    
    question = user['questions'][current_question_index]['question']
    
    # Cancel any existing question timer
    if user['question_timer']:
        user['question_timer'].cancel()

    # Start a new question timer
    user['question_timer'] = Timer(QUESTION_TIMER_DURATION, question_timeout, args=[user_id, chat_id, context])
    user['question_timer'].start()
    
    # Send the question and store the message ID
    message = await update.message.reply_text(question)
    user['question_message_id'] = message.message_id
    
    # Start or restart the points decrease timer
    if user['timer']:
        user['timer'].cancel()
    user['timer'] = Timer(POINTS_DECREASE_INTERVAL, decrease_points, args=[user_id, chat_id])
    user['timer'].start()

async def stop(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if chat_id not in user_data or user_id not in user_data[chat_id]:
        await update.message.reply_text('You are not currently in a quiz session.')
        return
    
    user = user_data[chat_id][user_id]
    
    # Cancel any existing timers
    if user['question_timer']:
        user['question_timer'].cancel()
    if user['timer']:
        user['timer'].cancel()
    
    # Delete the current question message if it exists
    if user['question_message_id']:
        await context.bot.delete_message(chat_id=chat_id, message_id=user['question_message_id'])
    
    # Clear user data
    del user_data[chat_id][user_id]
    
    await update.message.reply_text('The quiz has been stopped. Type /start to begin a new quiz session.')

async def handle_answer(update: Update, context: CallbackContext) -> None:
    global top_user
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if chat_id not in user_data or user_id not in user_data[chat_id]:
        await update.message.reply_text('Please start by typing /start')
        return
    
    user_answer = update.message.text
    user = user_data[chat_id][user_id]
    current_question_index = user['current_question']
    
    if current_question_index >= len(user['questions']):
        await update.message.reply_text('The quiz is already finished.')
        return
    
    question_data = user['questions'][current_question_index]
    correct_answer = question_data['answer']
    clue = question_data['clue']
    
    if user_answer.lower() == correct_answer.lower():
        points = random.randint(MIN_POINTS, MAX_POINTS)  # Randomize points
        user['score'] += points
        await update.message.reply_text(f'Correct! You have {user["score"]} points.')
        user['current_question'] += 1
        user['incorrect_attempts'] = 0  # Reset incorrect attempts
        
        # Cancel the question timer
        if user['question_timer']:
            user['question_timer'].cancel()
        
        # Delete the current question message if it exists
        if user['question_message_id']:
            await context.bot.delete_message(chat_id=chat_id, message_id=user['question_message_id'])
        
        if user['current_question'] >= len(user['questions']):
            await update.message.reply_text(f'Quiz completed! Your final score is {user["score"]}.')
            # Automatically stop the bot after quiz
            application.stop()
        else:
            next_question = user['questions'][user['current_question']]['question']
            message = await update.message.reply_text(next_question)
            user['question_message_id'] = message.message_id
        
        # Update top user for the chat
        if user['score'] > top_user['score']:
            top_user = {"chat_id": chat_id, "id": user_id, "score": user['score']}
    else:
        user['incorrect_attempts'] += 1
        if user['incorrect_attempts'] == 3:
            await update.message.reply_text(f'Incorrect. The correct answer is "{correct_answer}".')
            user['current_question'] += 1
            user['incorrect_attempts'] = 0  # Reset incorrect attempts
            
            # Cancel the question timer
            if user['question_timer']:
                user['question_timer'].cancel()
            
            # Delete the current question message if it exists
            if user['question_message_id']:
                await context.bot.delete_message(chat_id=chat_id, message_id=user['question_message_id'])
            
            if user['current_question'] >= len(user['questions']):
                await update.message.reply_text(f'Quiz completed! Your final score is {user["score"]}.')
                # Automatically stop the bot after quiz
                application.stop()
            else:
                next_question = user['questions'][user['current_question']]['question']
                message = await update.message.reply_text(next_question)
                user['question_message_id'] = message.message_id
        else:
            await update.message.reply_text(f'Incorrect. Here\'s a clue: {clue}. Try again!')

async def top(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if top_user['chat_id'] != chat_id or top_user['id'] is None:
        await update.message.reply_text('No top user yet in this chat.')
    else:
        top_user_name = (await context.bot.get_chat_member(chat_id=chat_id, user_id=top_user['id'])).user.full_name
        await update.message.reply_text(f'The top user is {top_user_name} with {top_user["score"]} points.')

async def reset_scores(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMINS:
        await update.message.reply_text('You do not have permission to use this command.')
        return
    
    if chat_id in user_data:
        user_data[chat_id] = {}
        global top_user
        top_user = {"chat_id": None, "id": None, "score": 0}
        await update.message.reply_text('Scores have been reset for this chat.')
    else:
        await update.message.reply_text('No quiz data found for this chat.')

async def review_scores(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in ADMINS:
        await update.message.reply_text('You do not have permission to use this command.')
        return
    
    if chat_id not in user_data or not user_data[chat_id]:
        await update.message.reply_text('No quiz data found for this chat.')
        return
    
    scores = [f'User {user_id}: {data["score"]} points' for user_id, data in user_data[chat_id].items()]
    scores_text = '\n'.join(scores) if scores else 'No scores available.'
    await update.message.reply_text('Current scores:\n' + scores_text)

async def ignore_message(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if chat_id in user_data and user_id in user_data[chat_id]:
        await update.message.reply_text('Please respond with your answer to the current question. Type /quiz to get the next question.')
    else:
        await update.message.reply_text('The quiz is not active. Type /start to begin the quiz.')

async def error_handler(update: Update, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    await update.message.reply_text("An error occurred. Please try again later.")

def main() -> None:
    global application
    token = os.getenv('BOT_TOKEN')
    if not token:
        logger.error("Bot token not found. Please set the BOT_TOKEN environment variable.")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("quiz", quiz))
    application.add_handler(CommandHandler("stop", stop))  # Add stop command handler
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("reset_scores", reset_scores))
    application.add_handler(CommandHandler("review_scores", review_scores))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    
    application.add_error_handler(error_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
