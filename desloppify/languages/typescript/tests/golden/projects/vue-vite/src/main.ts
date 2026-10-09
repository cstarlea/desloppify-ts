import { createApp } from 'vue'
import App from './App.vue'
import { globalComponents } from './plugins/register'
import { router } from './router'

const app = createApp(App)
for (const [name, component] of Object.entries(globalComponents)) {
  app.component(name, component)
}
app.use(router).mount('#app')
