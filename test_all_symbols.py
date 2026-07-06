import asyncio
from database.repository import get_candles, init_db
from neural.trainer import NeuralTrainer
from utils.config import NN_LOOKBACK, SYMBOLS

async def main():
    await init_db()
    trainer = NeuralTrainer()
    trainer.load_best()
    
    for symbol in SYMBOLS:
        candles = await get_candles(symbol, 60, limit=NN_LOOKBACK+50)
        res = trainer.predict(candles)
        print(f"{symbol}: {res}")

asyncio.run(main())
