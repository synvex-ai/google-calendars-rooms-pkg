from typing import Any, Callable


class ToolRegistry:
    def __init__(self):
        self.functions: dict[str, Callable] = {}
        self.tool_definitions: dict[str, dict[str, Any]] = {}
        self.tool_max_retries: dict[str, int] = {}

    def register_tools(self, tool_functions: dict[str, Callable], tool_descriptions: dict[str, str] = None, tool_max_retries: dict[str, int] = None):
        tool_descriptions = tool_descriptions or {}
        tool_max_retries = tool_max_retries or {}

        for action_name, func in tool_functions.items():
            if action_name in tool_descriptions:
                custom_description = tool_descriptions[action_name]
            else:
                if "::" in action_name:
                    addon_name = action_name.split("::")[0]
                    custom_description = f"Execute {action_name.split('::')[-1]} action from {addon_name} addon"
                else:
                    custom_description = f"Execute {action_name} action"

            max_retry = tool_max_retries.get(action_name, 0)
            self.tool_max_retries[action_name] = max_retry
            self._register_single_tool(action_name, func, custom_description)

    def _register_single_tool(self, action_name: str, func: Callable, context: str):
        self.functions[action_name] = func

        self.tool_definitions[action_name] = {
            "name": action_name,
            "description": context or f"Execute {action_name} action",
            "input_schema": self._convert_annotations_to_schema(func)
        }

    def _convert_annotations_to_schema(self, func: Callable) -> dict[str, Any]:
        try:
            import inspect

            from pydantic import create_model

            sig = inspect.signature(func)
            if not sig.parameters:
                return {"type": "object", "properties": {}, "required": []}

            fields = {}
            for param_name, param in sig.parameters.items():
                if param.annotation == inspect.Parameter.empty:
                    fields[param_name] = (Any, ...)
                else:
                    if param.default == inspect.Parameter.empty:
                        fields[param_name] = (param.annotation, ...)
                    else:
                        fields[param_name] = (param.annotation, param.default)

            DynamicModel = create_model('DynamicToolSchema', **fields)
            schema = DynamicModel.model_json_schema()

            if "properties" not in schema:
                schema["properties"] = {}
            if "required" not in schema:
                schema["required"] = []
            if "type" not in schema:
                schema["type"] = "object"

            return schema

        except Exception as e:
            from loguru import logger
            logger.warning(f"Pydantic schema generation failed for function '{func.__name__}': {str(e)}")
            logger.warning(f"Falling back to basic type converter for function '{func.__name__}'")
            return self._basic_type_converter(func)

    def _basic_type_converter(self, func: Callable) -> dict[str, Any]:

        if not hasattr(func, '__annotations__'):
            return {"type": "object", "properties": {}, "required": []}

        annotations = func.__annotations__
        schema = {
            "type": "object",
            "properties": {},
            "required": []
        }

        for param_name, param_type in annotations.items():
            if param_name == 'return':
                continue

            if param_type is str:
                schema["properties"][param_name] = {"type": "string"}
            elif param_type is int:
                schema["properties"][param_name] = {"type": "integer"}
            elif param_type is float:
                schema["properties"][param_name] = {"type": "number"}
            elif param_type is bool:
                schema["properties"][param_name] = {"type": "boolean"}
            elif param_type is dict or str(param_type) == "<class 'dict'>":
                schema["properties"][param_name] = {"type": "object"}
            else:
                from loguru import logger
                logger.warning(f"Unknown type '{param_type}' for parameter '{param_name}' in function '{func.__name__}', defaulting to string")
                schema["properties"][param_name] = {"type": "string"}

            schema["required"].append(param_name)

        return schema

    def get_tools_for_action(self) -> dict[str, Any]:
        return self.tool_definitions.copy()

    def get_function(self, action_name: str) -> Callable:
        return self.functions.get(action_name)

    def get_max_retries(self, action_name: str) -> int:
        return self.tool_max_retries.get(action_name, 0)

    def clear(self):
        self.functions.clear()
        self.tool_definitions.clear()
        self.tool_max_retries.clear()
