# Third-Party Software Notices and Licenses

This project incorporates third-party open source software. This file documents the third-party components, their licenses, and applicable copyright notices.

---

## Frontend Components (`frontend/agent-designer/`)

### `@xyflow/react` (React Flow)
- **License**: MIT License
- **Copyright**: (c) 2019-present webkid GmbH / Moritz Klack & Christopher Lütkenhaus
- **Repository**: https://github.com/xyflow/xyflow
- **Notice on Attribution**: In accordance with the MIT license and the project design specification (Fix 11 / `docs/designer/baseline.md`), React Flow attribution is maintained. When attribution removal is configured via `proOptions={{ hideAttribution: true }}`, users and operators should note webkid GmbH's subscription guidelines for production use. All copyright and license notices are retained here.

### `react` & `react-dom`
- **License**: MIT License
- **Copyright**: (c) Meta Platforms, Inc. and affiliates.
- **Repository**: https://github.com/facebook/react

### `zustand`
- **License**: MIT License
- **Copyright**: (c) 2019 Paul Henschel
- **Repository**: https://github.com/pmndrs/zustand

### `@radix-ui/react-*` (`react-dialog`, `react-dropdown-menu`, `react-tabs`, `react-tooltip`)
- **License**: MIT License
- **Copyright**: (c) 2022 WorkOS
- **Repository**: https://github.com/radix-ui/primitives

### `lucide-react`
- **License**: ISC License
- **Copyright**: (c) 2022 Lucide Contributors, (c) 2020 Cole Bemis
- **Repository**: https://github.com/lucide-icons/lucide

### `codemirror` & `@codemirror/lang-markdown`
- **License**: MIT License
- **Copyright**: (c) 2018-2021 by Marijn Haverbeke and others
- **Repository**: https://github.com/codemirror/dev

### `@tanstack/react-query`
- **License**: MIT License
- **Copyright**: (c) 2020-present Tanner Linsley
- **Repository**: https://github.com/TanStack/query

### `react-router-dom`
- **License**: MIT License
- **Copyright**: (c) React Training LLC 2015-2019, (c) Shopify Inc. 2020-2024
- **Repository**: https://github.com/remix-run/react-router

### `vite` & `@vitejs/plugin-react`
- **License**: MIT License
- **Copyright**: (c) 2019-present Yuxi (Evan) You and Vite contributors
- **Repository**: https://github.com/vitejs/vite

### `vitest` & `@testing-library/*`
- **License**: MIT License
- **Copyright**: (c) 2021-present Anthony Fu and Vitest contributors / (c) 2017 Kent C. Dodds
- **Repository**: https://github.com/vitest-dev/vitest, https://github.com/testing-library

---

## Backend Components (`pyproject.toml`)

### `fastapi`
- **License**: MIT License
- **Copyright**: (c) 2018 Sebastián Ramírez
- **Repository**: https://github.com/fastapi/fastapi

### `uvicorn`
- **License**: BSD 3-Clause License
- **Copyright**: (c) 2017-present, Encode OSS Ltd. All rights reserved.
- **Repository**: https://github.com/encode/uvicorn

### `pydantic` & `pydantic-settings`
- **License**: MIT License
- **Copyright**: (c) 2017-present Samuel Colvin and contributors
- **Repository**: https://github.com/pydantic/pydantic

### `psycopg` (`psycopg[binary,pool]`)
- **License**: GNU Lesser General Public License v3.0 (LGPLv3) / BSD 3-Clause (for C wrapper / bindings)
- **Copyright**: (c) 2001-2024 Federico Di Gregorio, Daniele Varrazzo and contributors
- **Repository**: https://github.com/psycopg/psycopg

### `httpx`
- **License**: BSD 3-Clause License
- **Copyright**: (c) 2019 Encode OSS Ltd.
- **Repository**: https://github.com/encode/httpx

### `orjson`
- **License**: Apache License 2.0 / MIT License
- **Copyright**: (c) 2018-2024 Iwan Bitarov
- **Repository**: https://github.com/ijl/orjson

### `pyyaml`
- **License**: MIT License
- **Copyright**: (c) 2017-2020 Ingy döt Net, (c) 2006-2016 Kirill Simonov
- **Repository**: https://github.com/yaml/pyyaml

### `deepagents`, `langchain-*`, `langgraph-*`
- **License**: MIT License
- **Copyright**: (c) 2023-present LangChain, Inc.
- **Repository**: https://github.com/langchain-ai

---

## Standard License Texts

### MIT License
```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### ISC License
```text
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

### BSD 3-Clause License
```text
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
