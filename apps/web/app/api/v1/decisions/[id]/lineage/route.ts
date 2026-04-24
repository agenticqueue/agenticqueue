import { NextRequest, NextResponse } from "next/server";

import {
  buildDecisionLineage,
  loadDecisionDataset,
  UpstreamError,
} from "../../data";
import {
  authHeadersFromRequest,
  unauthorizedSessionResponse,
} from "../../../../_upstream";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  const authHeaders = authHeadersFromRequest(request);
  if (!authHeaders) {
    return unauthorizedSessionResponse();
  }

  try {
    const { id } = await context.params;
    const dataset = await loadDecisionDataset({
      authHeaders,
      signal: request.signal,
    });
    const lineage = buildDecisionLineage(id, dataset);

    if (!lineage) {
      return NextResponse.json(
        { error: "Decision lineage was not found." },
        { status: 404 },
      );
    }

    return NextResponse.json(lineage);
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
            : "Failed to load decision lineage.",
      },
      { status: 500 },
    );
  }
}
