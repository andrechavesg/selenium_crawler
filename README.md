## 🕷️ Web Crawler Avançado

### 🚀 Recursos Principais

- **Renderização Dinâmica**: Suporte a páginas com JavaScript
- **Múltiplos Domínios**: Crawling flexível 
- **Controle Preciso**: Parametrização de profundidade e páginas
- **Tolerante a Falhas**: Fallback entre Selenium e requests

### 🛠 Uso Rápido

```bash
# Crawler para um domínio específico
docker-compose run crawler exemplo.com.br

# Configurações personalizadas
docker-compose run crawler exemplo.com.br --max-pages 20 --max-depth 4
docker-compose run crawler exemplo.com.br --max-pages 10 --max-depth 2
```

### 🔧 Parâmetros

| Parâmetro | Descrição | Padrão |
|-----------|-----------|--------|
| `--output-dir` | Diretório de resultados | `crawled_data` |
| `--js-render-time` | Tempo renderização JS | 3s |
| `--delay` | Intervalo requisições | 1s |
| `--max-pages` | Páginas máximas | 50 |
| `--max-depth` | Profundidade crawling | 2 |

### ⚠️ Avisos

- Respeite `robots.txt`
- Configure delays adequados
- Use eticamente