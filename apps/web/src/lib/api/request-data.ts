import "server-only";

import { cache } from "react";

import { getAgentConfig } from "@/lib/api/agent";

export const getAgentConfigForRequest = cache(getAgentConfig);
