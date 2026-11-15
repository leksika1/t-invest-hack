from fastapi import APIRouter

from app.cards.schema import BondCard, CardRequest

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.post("", response_model=BondCard)
def build_card(req: CardRequest) -> BondCard:
    # Stage 2 keeps this synchronous compatibility response; assembly is stage 3.
    return BondCard(isin=req.isin)
