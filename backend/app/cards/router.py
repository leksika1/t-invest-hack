from fastapi import APIRouter

from app.cards.schema import BondCard, CardRequest

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.post("", response_model=BondCard)
def build_card(req: CardRequest) -> BondCard:
    # Заглушка: сбор документов, LLM-извлечение и сверка с ISS будут добавлены позже.
    return BondCard(isin=req.isin)
