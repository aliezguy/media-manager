<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

// 修正了之前的 # 注释错误
const API_URL = import.meta.env.VITE_API_URL || '' 

// 默认选中 'category_yaml'
const activeTab = ref('category_yaml')
const fileContent = ref('')
const loading = ref(false)

const loadFile = async () => {
  loading.value = true
  try {
    const res = await axios.get(`${API_URL}/api/editor/${activeTab.value}`)
    fileContent.value = res.data.content
  } catch (e) {
    ElMessage.error('加载文件失败')
  } finally {
    loading.value = false
  }
}

const saveFile = async () => {
  try {
    await axios.post(`${API_URL}/api/editor/${activeTab.value}`, {
      content: fileContent.value
    })
    ElMessage.success('保存成功！配置已更新')
  } catch (e) {
    ElMessage.error('保存失败')
  }
}

onMounted(loadFile)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="header">
        <span>⚙️ 策略配置编辑器</span>
        <el-button type="primary" size="small" @click="saveFile">保存配置</el-button>
      </div>
    </template>
    
    <el-tabs v-model="activeTab" @tab-change="loadFile">
      <el-tab-pane label="分类策略 (category.yaml)" name="category_yaml"></el-tab-pane>
      </el-tabs>

    <el-input
      v-model="fileContent"
      type="textarea"
      :rows="25"
      placeholder="加载中..."
      v-loading="loading"
      style="font-family: monospace; font-size: 14px; line-height: 1.5;"
    />
  </el-card>
</template>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
</style>