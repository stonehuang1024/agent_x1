"""
Test script for Kimi API via Anthropic-compatible endpoint.

Environment variables:
- ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
- ANTHROPIC_API_KEY=sk-kimi-
- Model: anthropic/kimi-k2.5 or kimi-k2.5
"""

import os
import sys
import json
import logging
import requests
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_direct_http_request():
    """Test API using direct HTTP request."""
    base_url = "https://api.kimi.com/coding/"
    api_key = "sk-kimi-"
    
    # Try different model name formats
    model_names = [
        "kimi-k2.5",
        "anthropic/kimi-k2.5",
        "kimi-k2-turbo-preview",
        "moonshot-v1-8k"
    ]
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    for model in model_names:
        logger.info(f"\n{'='*60}")
        logger.info(f"Testing model: {model}")
        logger.info(f"{'='*60}")
        
        # Try OpenAI-compatible endpoint (chat/completions)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello, what is 2+2?"}
            ],
            "temperature": 0.7,
            "max_tokens": 100
        }
        
        # Try chat/completions endpoint
        url = f"{base_url}chat/completions"
        logger.info(f"Trying URL: {url}")
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            logger.info(f"Status Code: {response.status_code}")
            logger.info(f"Response Headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"SUCCESS! Response: {json.dumps(data, indent=2)[:500]}")
                return True, model, "chat/completions"
            else:
                logger.error(f"Error: {response.status_code}")
                logger.error(f"Response: {response.text[:500]}")
                
        except Exception as e:
            logger.error(f"Exception: {e}")
    
    return False, None, None


def test_anthropic_sdk_style():
    """Test API using Anthropic SDK style request."""
    base_url = "https://api.kimi.com/coding/"
    api_key = "sk-kimi-qtV7UODsmT6a9iymqtWRxYxGSp3REqdteqrN4Ajvd9xraPKdeEVBOoFdsg3aHeDG"
    
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    # Anthropic-style payload
    payload = {
        "model": "kimi-k2.5",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Hello, what is 2+2?"}
        ]
    }
    
    url = f"{base_url}v1/messages"
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing Anthropic SDK style")
    logger.info(f"URL: {url}")
    logger.info(f"{'='*60}")
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        logger.info(f"Status Code: {response.status_code}")
        logger.info(f"Response: {response.text[:1000]}")
        
        if response.status_code == 200:
            return True
            
    except Exception as e:
        logger.error(f"Exception: {e}")
    
    return False


def test_list_models():
    """Test listing available models."""
    base_url = "https://api.kimi.com/coding/"
    api_key = "sk-kimi-qtV7UODsmT6a9iymqtWRxYxGSp3REqdteqrN4Ajvd9xraPKdeEVBOoFdsg3aHeDG"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    url = f"{base_url}models"
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing models endpoint: {url}")
    logger.info(f"{'='*60}")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        logger.info(f"Status Code: {response.status_code}")
        logger.info(f"Response: {response.text[:1000]}")
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"Available models: {json.dumps(data, indent=2)}")
            
    except Exception as e:
        logger.error(f"Exception: {e}")


def main():
    """Run all tests."""
    logger.info("Starting Kimi API tests...")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Requests version: {requests.__version__}")
    
    # Test 1: Try listing models
    test_list_models()
    
    # Test 2: Try Anthropic SDK style
    test_anthropic_sdk_style()
    
    # Test 3: Try OpenAI-compatible style
    success, model, endpoint = test_direct_http_request()
    
    if success:
        logger.info(f"\n✅ SUCCESS! Working configuration:")
        logger.info(f"   Model: {model}")
        logger.info(f"   Endpoint: {endpoint}")
    else:
        logger.error("\n❌ All tests failed. Let's debug further...")
        
        # Try with different base URL variations
        logger.info("\nTrying URL variations...")
        
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
