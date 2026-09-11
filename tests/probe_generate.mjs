#!/usr/bin/env node
/**
 * /api/generate 接口能力探针 —— 检验当前模型对该接口的完整支持度。
 *
 * 用法::
 *
 *   node tests/probe_generate.mjs                    # 默认 11435（核显实例）
 *   node tests/probe_generate.mjs --port 11434       # 独显实例
 *   node tests/probe_generate.mjs --model qwen3.5:9b # 换模型做横向对照
 *
 * 逐项验证 /api/generate 的各项能力并给出 ✅ / ❌ / ⚠️ 判定：
 *   基础生成 / 流式 / raw 模式 / 模板渲染 / logprobs / num_predict /
 *   temperature 确定性 / stop 停止词 / num_ctx 上限 / keep_alive / format:json
 *
 * 零依赖，只用 Node 18+ 内置 fetch。
 */

const SEP = '='.repeat(74);
const LINE = '-'.repeat(74);

function parseArgs(argv) {
  const args = { port: 11435, model: 'qwen3-reranker:4b', timeout: 600000 };
  for (let i = 2; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--port') args.port = Number(argv[++i]);
    else if (flag === '--model') args.model = argv[++i];
    else if (flag === '--timeout') args.timeout = Number(argv[++i]);
    else if (flag === '--help' || flag === '-h') {
      console.log('用法: node tests/probe_generate.mjs [--port 11435] [--model 名称] [--timeout ms]');
      process.exit(0);
    } else {
      console.warn(`忽略未知参数: ${flag}`);
    }
  }
  return args;
}

const args = parseArgs(process.argv);
const BASE = `http://127.0.0.1:${args.port}`;

async function generate(payload, { stream = false } = {}) {
  const started = Date.now();
  const resp = await fetch(`${BASE}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, stream }),
    signal: AbortSignal.timeout(args.timeout),
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} ${resp.statusText}: ${text.slice(0, 200)}`);
  }
  const elapsed = (Date.now() - started) / 1000;
  if (!stream) return { data: JSON.parse(text), elapsed, chunks: null };
  // 流式：NDJSON，逐行解析
  const chunks = text
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l));
  return { data: chunks[chunks.length - 1], elapsed, chunks };
}

const ok = (detail) => ({ pass: true, detail });
const bad = (detail) => ({ pass: false, detail });
const info = (detail) => ({ pass: null, detail });

const CHECKS = [
  {
    name: '基础生成 (stream=false)',
    async run() {
      const { data, elapsed } = await generate({
        model: args.model,
        prompt: 'Say hello in one word:',
        raw: true,
        options: { temperature: 0, num_predict: 16 },
      });
      if (typeof data.response !== 'string' || !data.response.length) {
        return bad('response 为空');
      }
      if (data.done !== true) return bad(`done != true (${data.done})`);
      return ok(
        `response=${JSON.stringify(data.response.slice(0, 30))} ` +
          `done_reason=${data.done_reason} eval=${data.eval_count}tok ${elapsed.toFixed(2)}s`,
      );
    },
  },
  {
    name: '流式生成 (stream=true)',
    async run() {
      const { chunks, elapsed } = await generate(
        {
          model: args.model,
          prompt: 'Count from 1 to 5:',
          raw: true,
          options: { temperature: 0, num_predict: 24 },
        },
        { stream: true },
      );
      const texts = chunks.map((c) => c.response || '').join('');
      const last = chunks[chunks.length - 1];
      if (chunks.length < 2) return bad(`只收到 ${chunks.length} 个 chunk，未真正流式`);
      if (last.done !== true) return bad('最后一个 chunk done != true');
      return ok(`${chunks.length} 个 chunk, 拼接=${JSON.stringify(texts.slice(0, 30))}, ${elapsed.toFixed(2)}s`);
    },
  },
  {
    name: 'raw 模式 (绕过内嵌模板)',
    async run() {
      const prompt = '1+1=';
      const { data } = await generate({
        model: args.model,
        prompt,
        raw: true,
        options: { temperature: 0, num_predict: 8 },
      });
      // raw 模式下 Ollama 不应注入任何 <|im_start|> 之类的模板标记
      if (data.response.includes('<|im_start|>')) return bad('raw 模式下仍注入了模板标记');
      return ok(`prompt 原文续写生效，response=${JSON.stringify(data.response.slice(0, 24))}`);
    },
  },
  {
    name: '模板渲染 (raw=false)',
    async run() {
      const { data } = await generate({
        model: args.model,
        prompt: '你好',
        raw: false,
        options: { temperature: 0, num_predict: 64 },
      });
      const text = data.response || '';
      if (text.includes('<think>')) {
        return info(
          '模板生效但模型进入思考模式 —— GGUF 内嵌 chat_template 所致，非接口问题',
        );
      }
      return ok(`response=${JSON.stringify(text.slice(0, 30))}`);
    },
  },
  {
    name: 'logprobs + top_logprobs',
    async run() {
      const { data } = await generate({
        model: args.model,
        prompt: 'yes or no? Answer:',
        raw: true,
        logprobs: true,
        top_logprobs: 20,
        options: { temperature: 0, num_predict: 2 },
      });
      const positions = data.logprobs || [];
      if (!positions.length) return bad('未返回 logprobs（旧版 Ollama 会静默忽略该字段）');
      const first = positions[0] || {};
      const alts = first.top_logprobs || [];
      if (!alts.length) return bad('logprobs 存在但缺少 top_logprobs 备选');
      const top3 = alts
        .slice(0, 3)
        .map((a) => `${JSON.stringify(a.token)}:${a.logprob.toFixed(2)}`)
        .join(' ');
      return ok(`${positions.length} 个位置, top3 = ${top3}`);
    },
  },
  {
    name: 'num_predict 上限',
    async run() {
      const { data } = await generate({
        model: args.model,
        prompt: 'Continue: the quick brown fox',
        raw: true,
        options: { temperature: 0, num_predict: 4 },
      });
      const used = data.eval_count ?? -1;
      if (used > 4) return bad(`eval_count=${used} 超过 num_predict=4`);
      return ok(`eval_count=${used} ≤ 4, done_reason=${data.done_reason}`);
    },
  },
  {
    name: 'temperature=0 确定性',
    async run() {
      const payload = {
        model: args.model,
        prompt: 'Answer with one word: capital of France is',
        raw: true,
        options: { temperature: 0, num_predict: 8, seed: 42 },
      };
      const a = (await generate(payload)).data.response;
      const b = (await generate(payload)).data.response;
      if (a !== b) return bad(`两次结果不一致: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`);
      return ok(`两次一致: ${JSON.stringify(a.slice(0, 24))}`);
    },
  },
  {
    name: 'stop 停止词',
    async run() {
      const { data } = await generate({
        model: args.model,
        prompt: 'List: apple, banana, END, orange, grape',
        raw: true,
        options: { temperature: 0, num_predict: 40, stop: ['END'] },
      });
      const text = data.response || '';
      if (text.includes('END')) return bad(`停止词出现在输出中: ${JSON.stringify(text.slice(0, 40))}`);
      if (data.done_reason === 'stop') return ok(`命中停止词后终止，输出=${JSON.stringify(text.slice(0, 30))}`);
      return info(`未触发停止词（done_reason=${data.done_reason}），输出=${JSON.stringify(text.slice(0, 30))}`);
    },
  },
  {
    name: 'num_ctx 上限 (4096)',
    async run() {
      // 构造明显超过 4096 token 的 prompt，看是优雅报错还是静默截断
      const long = '这是一段用于测试上下文窗口长度的中文填充文本。'.repeat(1200);
      const { data } = await generate({
        model: args.model,
        prompt: long,
        raw: true,
        options: { temperature: 0, num_predict: 8, num_ctx: 4096 },
      });
      if (data.error) return ok(`超限被明确拒绝: ${String(data.error).slice(0, 60)}`);
      const used = data.prompt_eval_count ?? 0;
      return ok(`prompt_eval=${used}tok（模型按 num_ctx 截断/处理，未崩溃）`);
    },
  },
  {
    name: 'keep_alive 保活',
    async run() {
      await generate({
        model: args.model,
        prompt: 'hi',
        raw: true,
        keep_alive: '2m',
        options: { temperature: 0, num_predict: 4 },
      });
      const resp = await fetch(`${BASE}/api/ps`, { signal: AbortSignal.timeout(30000) });
      const ps = await resp.json();
      const hit = (ps.models || []).find((m) => m.name.startsWith(args.model.split(':')[0]));
      if (!hit) return bad(`/api/ps 中找不到已加载的 ${args.model}`);
      return ok(`/api/ps 显示 ${hit.name} 驻留 ${(hit.size_vram / 1e9).toFixed(1)}GB`);
    },
  },
  {
    name: 'format: json 结构化输出',
    async run() {
      const { data } = await generate({
        model: args.model,
        prompt: 'Return {"ok": true} as JSON only.',
        raw: true,
        format: 'json',
        options: { temperature: 0, num_predict: 32 },
      });
      try {
        JSON.parse(data.response);
        return ok(`返回合法 JSON: ${data.response.slice(0, 40)}`);
      } catch {
        return info(`未返回合法 JSON（reranker 类模型常见）: ${JSON.stringify(data.response.slice(0, 40))}`);
      }
    },
  },
];

async function main() {
  console.log(SEP);
  console.log(`/api/generate 能力探针    目标: ${BASE}    模型: ${args.model}`);
  console.log(SEP);

  const rows = [];
  for (const check of CHECKS) {
    process.stdout.write(`  ${check.name.padEnd(28)} ... `);
    try {
      const res = await check.run();
      const mark = res.pass === true ? '✅' : res.pass === false ? '❌' : '⚠️ ';
      console.log(`${mark}  ${res.detail}`);
      rows.push({ name: check.name, pass: res.pass, detail: res.detail });
    } catch (error) {
      console.log(`❌  ${error.message}`);
      rows.push({ name: check.name, pass: false, detail: error.message });
    }
  }

  const passed = rows.filter((r) => r.pass === true).length;
  const failed = rows.filter((r) => r.pass === false).length;
  const warned = rows.filter((r) => r.pass === null).length;

  console.log(LINE);
  console.log(`通过 ${passed} / 失败 ${failed} / 不适用或需注意 ${warned}`);
  if (failed === 0) {
    console.log('结论: /api/generate 核心能力全部可用 ✅');
  } else {
    console.log('结论: 存在失败项，详见上方 ❌ 条目');
  }
  console.log(SEP);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error(`\n探针执行失败: ${error.message}`);
  process.exit(2);
});
