import asyncio
from database.repository import get_candles, init_db
from neural.trainer import NeuralTrainer
from utils.config import NN_LOOKBACK

async def main():
    await init_db()
    candles = await get_candles('1HZ50V', 60, limit=NN_LOOKBACK+50)
    print(f"Candles fetched: {len(candles)}")
    
    trainer = NeuralTrainer()
    loaded = trainer.load_best()
    print(f"Model loaded: {loaded}")
    
    res = trainer.predict(candles)
    print(f"Prediction result: {res}")

asyncio.run(main())
