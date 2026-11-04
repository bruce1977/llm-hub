// Prop contracts for the presentational components. Kept out of the SFCs because
// `<script setup>` cannot export types, and the views need to build these shapes.

import type { Badge } from '@/utils/format'

/** One line of `ConfigCard`: a label on the left, a value or a badge on the right. */
export interface ConfigRow {
  label: string
  value?: unknown
  mono?: boolean
  badge?: Badge | null
  note?: string
}

/** One row of `ApiDoc`'s parameter table. */
export interface ApiParam {
  name: string
  type: string
  required: boolean
  desc: string
}

/** The request half of `ApiDoc`: method, path, headers and an example body. */
export interface ApiRequestSpec {
  method: string
  path: string
  headers: string[][]
  body: unknown
}

/** The response half of `ApiDoc`: status, content type and an example body. */
export interface ApiResponseSpec {
  status: string | number
  contentType: string
  note?: string
  body: unknown
}
