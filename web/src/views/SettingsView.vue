<script setup lang="ts">
import TopBar from '@/components/TopBar.vue'

const upcoming = ['API Keys 管理', 'Rerank 配置', 'Security 规则', 'Playground 调试', '上游增删改']
</script>

<template>
  <TopBar title="Settings" sub="管理台设置" />

  <div class="soon-box">
    <div class="big">🚧</div>
    <div class="t">此模块即将开放</div>
    <div class="d">只读视图阶段不开放配置写入口。以下能力将在后续版本按需开放：</div>
    <div class="soon-list">
      <span v-for="item in upcoming" :key="item" class="chip s2">{{ item }}</span>
    </div>
  </div>

  <div class="foot">llm-hub 管理台 · Settings（只读视图）</div>
</template>
