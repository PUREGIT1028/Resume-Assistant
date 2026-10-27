from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
from loguru import logger
from config.config import settings
from app.usage import record_usage


class LLMClient:
    """
    LLM客户端连接模块
    """
    
    def __init__(self, model_name: Optional[str] = None, temperature: Optional[float] = None):
        """
        初始化LLM客户端
        
        Args:
            model_name (str): 模型名称，默认取配置 settings.LLM_MODEL
            temperature (float): 采样温度，默认取配置 settings.LLM_TEMPERATURE
        """
        model_name = model_name or settings.LLM_MODEL
        temperature = settings.LLM_TEMPERATURE if temperature is None else temperature

        # 获取 LLM 的 API 密钥和基础 URL（环境变量优先，回退到配置）
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or settings.LLM_API_KEY
        base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or settings.LLM_BASE_URL

        if not api_key:
            raise ValueError("LLM_API_KEY (or OPENAI_API_KEY) environment variable is not set")
        
        # This workflow needs extraction/reporting, not long reasoning.
        options = {}
        if base_url and 'bigmodel.cn' in base_url and model_name.startswith('glm-4.7'):
            options['extra_body'] = {'thinking': {'type': 'disabled'}}
        is_glm53 = bool(base_url and 'bigmodel.cn' in base_url and model_name.startswith('glm-5.3'))
        if is_glm53:
            options['extra_body'] = {'thinking': {'type': 'enabled'}}
            temperature = 1.0
        # 初始化模型
        self.model = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base=base_url,
            timeout=90.0 if is_glm53 else 30.0,
            max_retries=0,
            max_tokens=16384 if is_glm53 else 4096,
            **options,
        )
        self.model_name = model_name
        logger.info(f"Initialized LLM client with model: {model_name}")

    def generate_text(self, prompt: str, system_message: Optional[str] = None) -> str:
        """
        生成文本
        
        Args:
            prompt (str): 用户提示
            system_message (str, optional): 系统消息
            
        Returns:
            str: 生成的文本
        """
        try:
            if system_message:
                messages = [
                    SystemMessage(content=system_message),
                    HumanMessage(content=prompt)
                ]
            else:
                messages = [HumanMessage(content=prompt)]
                
            try:
                response = self.model.invoke(messages)
            except Exception:
                record_usage(getattr(self, 'model_name', 'unknown'), 'llm')
                raise
            usage = getattr(response, 'usage_metadata', None)
            record_usage(getattr(self, 'model_name', 'unknown'), 'llm', usage)
            if not isinstance(response.content, str) or not response.content.strip():
                raise ValueError('模型返回了空内容，请稍后重试；若持续发生，请检查模型服务状态。')
            logger.debug(f"Generated text with {len(response.content)} characters")
            return response.content
        except Exception as e:
            logger.warning("Model call failed ({})", type(e).__name__)
            raise

    def generate_with_template(self, template: str, **kwargs) -> str:
        """
        使用模板生成文本
        
        Args:
            template (str): 提示模板
            **kwargs: 模板变量
            
        Returns:
            str: 生成的文本
        """
        try:
            prompt = ChatPromptTemplate.from_template(template).format(**kwargs)
            response = self.generate_text(prompt)
            logger.debug(f"Generated text with template, response length: {len(response)}")
            return response
        except Exception as e:
            logger.error(f"Failed to generate text with template: {e}")
            raise