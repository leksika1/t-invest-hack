from decimal import Decimal

from app.config import Settings


def make(**env):
    return Settings(_env_file=None, **env)


def test_google_ai_studio_key_selects_google_endpoint_and_model():
    s = make(llm_api_key='AIzaSyTEST', llm_base_url='', llm_model='')
    assert s.llm_base_url == 'https://generativelanguage.googleapis.com/v1beta/openai'
    assert s.llm_model == 'gemini-2.5-flash'
    assert s.llm_input_price_per_1m_usd == Decimal('0.30')
    assert s.llm_output_price_per_1m_usd == Decimal('2.50')
    assert s.llm_configuration_error is None


def test_openrouter_key_keeps_openrouter():
    s = make(llm_api_key='sk-or-v1-TEST', llm_base_url='', llm_model='')
    assert s.llm_base_url == 'https://openrouter.ai/api/v1'
    assert s.llm_model == 'google/gemini-2.5-flash'


def test_explicit_values_win_over_key_detection():
    s = make(llm_api_key='AIzaSyTEST', llm_base_url='https://example.test/v1', llm_model='custom-model',
             llm_input_price_per_1m_usd='1', llm_output_price_per_1m_usd='2')
    assert s.llm_base_url == 'https://example.test/v1'
    assert s.llm_model == 'custom-model'


def test_no_key_defaults_to_openrouter():
    s = make(llm_api_key='', llm_base_url='', llm_model='')
    assert s.llm_base_url == 'https://openrouter.ai/api/v1'
    assert s.llm_model == 'google/gemini-2.5-flash'
