// Prop contracts for the presentational components. Kept out of the SFCs because
// `<script setup>` cannot export types, and the views need to build these shapes.

import type { Badge } from '@/utils/format'
import type { ApiTestKind } from '@/types/api'

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

/** How an `ApiTester` input is edited and, more importantly, turned back into JSON.
 *  `lines` becomes a string array, `json` is parsed; `bool` with a `def` is a checkbox that
 *  always sends true / false, while a `bool` without one stays a three-state select where
 *  "not set" sends nothing at all. */
export type TestFieldType = 'text' | 'textarea' | 'number' | 'bool' | 'lines' | 'json' | 'select'

/** One parameter of a tester. `key` is a dot path into the request body, so
 *  `options.temperature` lands in the nested object the endpoint actually reads. */
export interface TestField {
  key: string
  label: string
  type: TestFieldType
  /** The tester's initial value: filled in when it opens and restored by 「恢复默认值」.
   *  A `bool` with one becomes a checkbox that is always sent; without one it is a
   *  three-state select. `bool` and `select` take the string form (`'true'`, `'float'`). */
  def?: string
  required?: boolean
  /** Choices for `type: 'select'`. */
  options?: string[]
  /** Free-form note under the input, for a shape the field cannot express. */
  hint?: string
  /** Span both columns of the form (long text, JSON blocks). */
  wide?: boolean
}

/** The tester half of `ApiDoc`: which documented endpoint gets a live test window.
 *  `ApiTester` loads the kind's own context (models, defaults, masked keys) from the shared
 *  `/api-test/models` cache, so a view only has to name the kind and its parameters. */
export interface TestSpec {
  kind: ApiTestKind
  fields: TestField[]
}
