#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

# ============================================
# 设置数据目录（按开发文档标准）
# ============================================
# 优先从环境变量读取
TRIM_PKGVAR = os.environ.get('TRIM_PKGVAR')

if not TRIM_PKGVAR:
    # 如果环境变量没有，从应用路径推断
    APP_ROOT = os.path.dirname(CURRENT_DIR)  # 应用根目录
    APP_NAME = os.path.basename(APP_ROOT)
    
    # 尝试从存储池路径推断 @appdata
    # 1.1.0 修正：去掉 /var，保持与 1.0.5 实际运行路径一致
    if '/@appcenter/' in APP_ROOT:
        vol_path = APP_ROOT.split('/@appcenter/')[0]
        TRIM_PKGVAR = os.path.join(vol_path, '@appdata', APP_NAME)  # 不加 /var
    else:
        TRIM_PKGVAR = os.path.join(APP_ROOT, 'data')
    
    os.environ['TRIM_PKGVAR'] = TRIM_PKGVAR

# 确保数据目录存在
os.makedirs(TRIM_PKGVAR, exist_ok=True)
print(f"数据目录: {TRIM_PKGVAR}")

# ============================================
# 导入 app 模块（app.so）
# ============================================
try:
    import app
except ImportError as e:
    print(f"导入 app 模块失败: {e}")
    sys.exit(1)

if __name__ == '__main__':
    port = int(os.environ.get('TRIM_SERVICE_PORT', 5665))
    print(f"启动云资源...")
    print(f"当前目录: {CURRENT_DIR}")
    print(f"服务端口: {port}")
    app.app.run(host='0.0.0.0', port=port, debug=False)