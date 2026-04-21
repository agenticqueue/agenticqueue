import { NextRequest, NextResponse } from "next/server";

import {
  buildDecisionItems,
  DecisionListResponse,
  loadDecisionDataset,
  UpstreamError,
} from "./data";

export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization) {
    return NextResponse.json(
      { error: "Authorization header is required." },
      { status: 401 },
    );
  }

  try {
    const dataset = await loadDecisionDataset({
      authorization,
      signal: request.signal,
    });
    const items = buildDecisionItems(dataset);

    return NextResponse.json<DecisionListResponse>({
      generated_at: new Date().toISOString(),
      count: items.length,
      items,
    });
  } catch (error: unknown) {
    if (error instanceof UpstreamError) {
      return NextResponse.json(
        { error: error.message, status: error.status },
        { status: error.status },
      );
    }

    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to aggregate decisions view.",
      },
      { status: 500 },
    );
  }
}
