from __future__ import annotations

import json
from os import PathLike
from typing import Any

from .client_types import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_MAIN_MODEL,
    DEFAULT_OPENAI_API_BASE_URL,
    ImageResult,
    image_model_supports_input_fidelity,
)
from .codex_responses_client import CodexImageClient
from .http import Transport, UrllibTransport
from .openai_images_client import OpenAIImagesImageClient

WEB_SEARCH_PROMPT_INSTRUCTIONS = (
    "Web search image prompt workflow:\n"
    "Use web_search to research the user's topic, then return only the final image-generation prompt. "
    "Do not return JSON unless the user explicitly asked the final image prompt to be JSON. "
    "Preserve the user's creative directions, layout, text requirements, size/aspect instructions, and hard constraints. "
    "Add concise factual context from search results only when it helps the requested image."
)

class OpenAIResponsesImageClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_API_BASE_URL,
        image_model: str = DEFAULT_IMAGE_MODEL,
        transport: Transport | None = None,
    ) -> None:
        clean_key = str(api_key or "").strip()
        if not clean_key:
            raise RuntimeError("OpenAI-compatible API key is required")
        self.api_key = clean_key
        self.base_url = OpenAIImagesImageClient._normalize_base_url(base_url)
        self.image_model = str(image_model or DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
        self.transport = transport or UrllibTransport()
        self.responses_url = f"{self.base_url}/responses"

    def generate_image(
        self,
        *,
        prompt: str,
        instructions: str | None = None,
        main_model: str = DEFAULT_MAIN_MODEL,
        model: str | None = None,
        reference_images: list[str] | None = None,
        size: str | None = None,
        quality: str | None = None,
        background: str | None = None,
        output_format: str = "png",
        moderation: str | None = None,
        output_compression: int | None = None,
        partial_images: int | None = None,
        web_search: bool = False,
        debug_sse_path: str | PathLike[str] | None = None,
    ) -> ImageResult:
        if web_search:
            searched_prompt, search_usage = self._request_web_search_prompt(
                prompt=prompt,
                instructions=instructions,
                main_model=main_model,
                debug_sse_path=debug_sse_path,
            )
            result = OpenAIImagesImageClient(
                api_key=self.api_key,
                base_url=self.base_url,
                image_model=self.image_model,
                transport=self.transport,
            ).generate_image(
                prompt=searched_prompt,
                main_model=main_model,
                model=model,
                reference_images=reference_images,
                size=size,
                quality=quality,
                background=background,
                output_format=output_format,
                moderation=moderation,
                output_compression=output_compression,
                partial_images=partial_images,
                debug_sse_path=debug_sse_path,
            )
            return self._with_combined_tool_usage(result, search_usage)
        action = "edit" if reference_images else "generate"
        payload = self.build_payload(
            prompt=prompt,
            instructions=instructions,
            action=action,
            main_model=main_model,
            model=model,
            input_images=reference_images or [],
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            moderation=moderation,
            output_compression=output_compression,
            partial_images=partial_images,
            web_search=web_search,
        )
        return self._request_and_parse(payload, debug_sse_path=debug_sse_path)

    def edit_image(
        self,
        *,
        prompt: str,
        images: list[str],
        mask_image: str | None = None,
        instructions: str | None = None,
        main_model: str = DEFAULT_MAIN_MODEL,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        background: str | None = None,
        output_format: str = "png",
        input_fidelity: str | None = None,
        moderation: str | None = None,
        output_compression: int | None = None,
        partial_images: int | None = None,
        web_search: bool = False,
        debug_sse_path: str | PathLike[str] | None = None,
    ) -> ImageResult:
        if not images:
            raise RuntimeError("edit_image requires at least one input image")

        if web_search:
            searched_prompt, search_usage = self._request_web_search_prompt(
                prompt=prompt,
                instructions=instructions,
                main_model=main_model,
                debug_sse_path=debug_sse_path,
            )
            result = OpenAIImagesImageClient(
                api_key=self.api_key,
                base_url=self.base_url,
                image_model=self.image_model,
                transport=self.transport,
            ).edit_image(
                prompt=searched_prompt,
                images=images,
                mask_image=mask_image,
                main_model=main_model,
                model=model,
                size=size,
                quality=quality,
                background=background,
                output_format=output_format,
                input_fidelity=input_fidelity,
                moderation=moderation,
                output_compression=output_compression,
                partial_images=partial_images,
                debug_sse_path=debug_sse_path,
            )
            return self._with_combined_tool_usage(result, search_usage)

        payload = self.build_payload(
            prompt=prompt,
            instructions=instructions,
            action="edit",
            main_model=main_model,
            model=model,
            input_images=images,
            mask_image=mask_image,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            input_fidelity=input_fidelity,
            moderation=moderation,
            output_compression=output_compression,
            partial_images=partial_images,
            web_search=web_search,
        )
        return self._request_and_parse(payload, debug_sse_path=debug_sse_path)

    def build_payload(
        self,
        *,
        prompt: str,
        instructions: str | None = None,
        action: str = "generate",
        main_model: str = DEFAULT_MAIN_MODEL,
        model: str | None = None,
        input_images: list[str] | None = None,
        mask_image: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        background: str | None = None,
        output_format: str = "png",
        input_fidelity: str | None = None,
        moderation: str | None = None,
        output_compression: int | None = None,
        partial_images: int | None = None,
        web_search: bool = False,
    ) -> dict[str, Any]:
        image_model = str(model or self.image_model or DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
        tool: dict[str, Any] = {
            "type": "image_generation",
            "action": action,
            "model": image_model,
            "output_format": output_format,
        }
        if size:
            tool["size"] = size
        if quality:
            tool["quality"] = quality
        if background:
            tool["background"] = background
        if input_fidelity and image_model_supports_input_fidelity(image_model):
            tool["input_fidelity"] = input_fidelity
        if moderation:
            tool["moderation"] = moderation
        if output_compression is not None:
            tool["output_compression"] = output_compression
        if partial_images is not None:
            tool["partial_images"] = partial_images
        if mask_image:
            tool["input_image_mask"] = {"image_url": mask_image}

        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        for image_url in input_images or []:
            content.append({"type": "input_image", "image_url": image_url})

        tools: list[dict[str, Any]] = [tool]
        tool_choice: Any = {"type": "image_generation"}
        if web_search:
            tools = [{"type": "web_search", "search_context_size": "low"}]
            tool_choice = "required"

        payload: dict[str, Any] = {
            "endpoint": "/responses",
            "stream": True,
            "model": main_model or DEFAULT_MAIN_MODEL,
            "store": False,
            "tool_choice": tool_choice,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            ],
            "tools": tools,
        }
        if web_search:
            payload["parallel_tool_calls"] = False
        if web_search:
            payload["instructions"] = self._web_search_prompt_instructions(instructions)
        elif instructions:
            payload["instructions"] = instructions
        return payload

    def _request_and_parse(
        self,
        payload: dict[str, Any],
        *,
        debug_sse_path: str | PathLike[str] | None = None,
    ) -> ImageResult:
        body = json.dumps(self._json_request_payload(payload)).encode("utf-8")
        response = self.transport.request(
            method="POST",
            url=self.responses_url,
            headers=self._build_headers(),
            body=body,
        )
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(
                "OpenAI-compatible responses request failed: "
                f"HTTP {response.status}: {response.body.decode('utf-8', errors='replace')}"
            )
        return self.parse_sse_response(response.body, debug_sse_path=debug_sse_path)

    def _request_web_search_prompt(
        self,
        *,
        prompt: str,
        instructions: str | None,
        main_model: str,
        debug_sse_path: str | PathLike[str] | None,
    ) -> tuple[str, dict[str, Any]]:
        payload = self.build_payload(
            prompt=prompt,
            instructions=instructions,
            main_model=main_model,
            model=self.image_model,
            output_format="png",
            web_search=True,
        )
        body = json.dumps(self._json_request_payload(payload)).encode("utf-8")
        response = self.transport.request(
            method="POST",
            url=self.responses_url,
            headers=self._build_headers(),
            body=body,
        )
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(
                "OpenAI-compatible web search request failed: "
                f"HTTP {response.status}: {response.body.decode('utf-8', errors='replace')}"
            )
        return self._parse_web_search_prompt_response(response.body, debug_sse_path=debug_sse_path)

    def _parse_web_search_prompt_response(
        self,
        body: bytes,
        *,
        debug_sse_path: str | PathLike[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        output_items_by_index: dict[int, dict[str, Any]] = {}
        output_items_fallback: list[dict[str, Any]] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == b"[DONE]":
                continue
            event = json.loads(payload.decode("utf-8"))
            if debug_sse_path is not None:
                CodexImageClient._write_sse_debug_event(debug_sse_path, event)
            event_type = event.get("type")
            if event_type == "error":
                raise RuntimeError(self._format_sse_error(event))
            if event_type in {"response.failed", "response.incomplete"}:
                raise RuntimeError(self._format_response_terminal_error(event))
            if event_type == "response.output_item.done":
                item = event.get("item")
                if not isinstance(item, dict):
                    continue
                index = event.get("output_index")
                if isinstance(index, int):
                    output_items_by_index[index] = item
                else:
                    output_items_fallback.append(item)
                continue
            if event_type != "response.completed":
                continue
            response = event.get("response", {})
            output = response.get("output") or self._reconstruct_output(output_items_by_index, output_items_fallback)
            text = self._extract_output_failure_message(output).strip()
            if not text:
                raise RuntimeError("OpenAI-compatible web search completed without a prompt")
            usage = response.get("usage")
            return text, dict(usage) if isinstance(usage, dict) else {}
        raise RuntimeError("No response.completed event found in web search SSE stream")

    @staticmethod
    def _web_search_prompt_instructions(instructions: str | None) -> str:
        base = str(instructions or "").strip()
        if base:
            return f"{base}\n\n{WEB_SEARCH_PROMPT_INSTRUCTIONS}"
        return WEB_SEARCH_PROMPT_INSTRUCTIONS

    @staticmethod
    def _with_combined_tool_usage(result: ImageResult, search_usage: dict[str, Any]) -> ImageResult:
        tool_usage = dict(result.tool_usage or {})
        if search_usage:
            tool_usage["web_search"] = search_usage
        if result.usage:
            tool_usage["image_gen"] = result.usage
        return ImageResult(
            image_bytes=result.image_bytes,
            revised_prompt=result.revised_prompt,
            output_format=result.output_format,
            size=result.size,
            background=result.background,
            quality=result.quality,
            usage=result.usage,
            tool_usage=tool_usage,
        )

    @staticmethod
    def _json_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key != "endpoint" and value is not None}

    def _build_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }

    parse_sse_response = CodexImageClient.parse_sse_response

    @staticmethod
    def _reconstruct_output(
        output_items_by_index: dict[int, dict[str, Any]],
        output_items_fallback: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return CodexImageClient._reconstruct_output(output_items_by_index, output_items_fallback)

    @staticmethod
    def _format_sse_error(event: dict[str, Any]) -> str:
        return CodexImageClient._format_sse_error(event)

    @staticmethod
    def _format_response_terminal_error(event: dict[str, Any]) -> str:
        return CodexImageClient._format_response_terminal_error(event)

    @staticmethod
    def _format_missing_image_call_error(output: Any) -> str:
        return CodexImageClient._format_missing_image_call_error(output)

    @staticmethod
    def _extract_output_failure_message(output: Any) -> str:
        return CodexImageClient._extract_output_failure_message(output)

    @staticmethod
    def _extract_message_text_parts(item: dict[str, Any]) -> list[str]:
        return CodexImageClient._extract_message_text_parts(item)

    @staticmethod
    def _extract_tool_failure_message(item: dict[str, Any]) -> str:
        return CodexImageClient._extract_tool_failure_message(item)

    @staticmethod
    def _extract_text_fields(payload: dict[str, Any], field_names: tuple[str, ...]) -> list[str]:
        return CodexImageClient._extract_text_fields(payload, field_names)

    @staticmethod
    def _join_unique_text_parts(parts: list[str]) -> str:
        return CodexImageClient._join_unique_text_parts(parts)

    @staticmethod
    def _extract_image_call(output: Any) -> dict[str, Any] | None:
        return CodexImageClient._extract_image_call(output)

    @staticmethod
    def _write_sse_debug_event(path: str | PathLike[str], event: dict[str, Any]) -> None:
        CodexImageClient._write_sse_debug_event(path, event)
