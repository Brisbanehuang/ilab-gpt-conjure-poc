export class JsonResponseParseError extends Error {
  response: Response;
  status: number;
  bodySnippet: string;

  constructor(response: Response, bodySnippet: string) {
    super("服务暂时不可用，请稍后重试");
    this.name = "JsonResponseParseError";
    this.response = response;
    this.status = response.status;
    this.bodySnippet = bodySnippet;
  }
}

export async function safeJson(response: Response): Promise<any> {
  const text = await response.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new JsonResponseParseError(response, text.slice(0, 240));
  }
}
