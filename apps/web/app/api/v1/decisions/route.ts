import { NextRequest, NextResponse } from "next/server";

import {
  buildDecisionItems,
  DecisionListResponse,
  loadDecisionDataset,
  UpstreamError,
} from "./data";
import {
  authHeadersFromRequest,
  unauthorizedSessionResponse,
} from "../../_upstream";

export async function GET(request: NextRequest) {
  const authHeaders = authHeadersFromRequest(request);
  if (!authHeaders) {
    return unauthorizedSessionResponse();
  }

  try {
    const dataset = await loadDecisionDataset({
      authHeaders,
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
