# ============================================
# 适配飞牛 Native 应用环境
# ============================================
import sys
import os
import hashlib
import base64

# ============================================
# 环境变量读取（按开发文档标准）
# ============================================
TRIM_APPDEST = os.environ.get('TRIM_APPDEST')
TRIM_PKGVAR = os.environ.get('TRIM_PKGVAR')
TRIM_SERVICE_PORT = os.environ.get('TRIM_SERVICE_PORT', '5665')

if not TRIM_APPDEST or not TRIM_PKGVAR:
    print("=" * 60)
    print("错误: 环境变量 TRIM_APPDEST 或 TRIM_PKGVAR 未设置")
    print(f"TRIM_APPDEST = {TRIM_APPDEST}")
    print(f"TRIM_PKGVAR = {TRIM_PKGVAR}")
    print("请通过飞牛应用中心启动应用")
    print("=" * 60)
    sys.exit(1)

os.makedirs(TRIM_PKGVAR, exist_ok=True)

# ============================================
# vendor 路径
# ============================================
VENDOR_PATH = os.path.join(TRIM_APPDEST, 'server', 'vendor')
if os.path.exists(VENDOR_PATH):
    sys.path.insert(0, VENDOR_PATH)

# ============================================
# import
# ============================================
from pathlib import Path
import json
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ============ Markdown 支持 ============
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False
    print('markdown 库未安装，Markdown 功能不可用')

# ============ Flask 初始化 ============
TEMPLATE_FOLDER = os.path.join(TRIM_APPDEST, 'server', 'templates')
app = Flask(__name__, template_folder=TEMPLATE_FOLDER)
app.secret_key = 'resource-station-secret-key-change-in-production'

# ============ 模板过滤器 ============
@app.template_filter('markdown')
def markdown_filter(text):
    if not text:
        return ''
    if MARKDOWN_AVAILABLE:
        return markdown.markdown(text, extensions=['extra', 'codehilite'])
    return text

# ============ 配置读取 ============
def load_app_config():
    config_file = os.path.join(TRIM_PKGVAR, 'app_config.json')
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'读取配置文件失败: {e}')
    return {}

def get_upload_folder():
    upload_path = os.environ.get('wizard_upload_path')
    if upload_path:
        return upload_path
    config = load_app_config()
    if config.get('upload_path'):
        return config.get('upload_path')
    default_path = os.path.join(TRIM_PKGVAR, 'uploads')
    os.makedirs(default_path, exist_ok=True)
    return default_path


# ============================================
# 辅助函数
# ============================================
def get_safe_display_name(filename):
    """过滤非法字符，保留文件名用于显示"""
    name = re.sub(r'[<>:"/\\|?*]', '_', filename).strip()
    return name if name else 'file'


def get_unique_filepath(directory, filename):
    """检查文件是否存在，如果存在则自动添加 (1)、(2)..."""
    base, ext = os.path.splitext(filename)
    new_filename = filename
    counter = 1
    while os.path.exists(os.path.join(directory, new_filename)):
        new_filename = f"{base} ({counter}){ext}"
        counter += 1
    return os.path.join(directory, new_filename)


# ============ 配置 ============
SECRET_KEY = 'cloud-resources-hhxs2025-2026abcd0002'

UPLOAD_FOLDER = get_upload_folder()
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_PATH = Path(TRIM_PKGVAR) / 'resource.db'
ALLOWED_EXTENSIONS = {'zip', 'rar', '7z', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'jpg', 'png', 'mp4', 'exe', 'msi'}

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'
login_manager.login_message = '请先登录'
login_manager.login_message_category = 'warning'


# ============ 授权核心 ============
def get_machine_id():
    try:
        with open('/etc/machine-id', 'r') as f:
            return f.read().strip()
    except:
        return None


def is_app_activated():
    """检查应用是否已激活"""
    machine_id = get_machine_id()
    if not machine_id:
        return False
    lic = License.get_license(machine_id)
    return lic and lic.is_valid and lic.license_type != 'none'


def verify_license_code(software_name, secret_key, license_code, machine_code):
    if not license_code or not secret_key or not machine_code:
        return False, "参数不完整"
    try:
        decoded = base64.b64decode(license_code).decode()
        if '||' in decoded:
            data_str, signature = decoded.split('||', 1)
            expected = hashlib.sha256(f"{data_str}{secret_key}".encode()).hexdigest()
            if signature == expected:
                data = json.loads(data_str)
                if data.get('software') == software_name:
                    expire = data.get('expire')
                    if expire and expire != 'permanent':
                        try:
                            expire_date = datetime.strptime(expire, '%Y-%m-%d')
                            if datetime.now() > expire_date:
                                return False, f"注册码已过期 (过期日期: {expire})"
                        except ValueError:
                            return False, "注册码日期格式无效"
                    return True, {"type": "enhanced", "data": data}
    except:
        pass
    try:
        expected_simple = hashlib.sha256(f"{secret_key}|{machine_code}".encode()).hexdigest()[:32]
        if license_code.strip().lower() == expected_simple.lower():
            return True, {"type": "simple", "expire": "permanent"}
    except:
        pass
    return False, "注册码无效"


# ============ 数据模型 ============
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    # 1.2.0 新增：下载权限控制（仅按分类）
    download_categories = db.Column(db.Text, default='')  # 逗号分隔的分类ID，空=全部

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def can_download_resource(self, resource):
        """检查当前用户是否有权限下载指定资源"""
        if self.is_admin:
            return True

        # 未激活状态下，所有用户无限制
        if not is_app_activated():
            return True

        # 分类权限检查
        if self.download_categories:
            allowed_categories = [int(x.strip()) for x in self.download_categories.split(',') if x.strip()]
            if allowed_categories and resource.category_id not in allowed_categories:
                return False

        return True

    def get_allowed_resource_query(self, query):
        """根据用户权限过滤资源查询"""
        if self.is_admin:
            return query

        # 未激活状态下，无过滤
        if not is_app_activated():
            return query

        if self.download_categories:
            allowed = [int(x.strip()) for x in self.download_categories.split(',') if x.strip()]
            if allowed:
                query = query.filter(Resource.category_id.in_(allowed))

        return query


class SiteConfig(db.Model):
    __tablename__ = 'site_config'
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(100), default='云资源')
    site_intro = db.Column(db.Text, default='欢迎来到云资源')
    about_content = db.Column(db.Text, default='<h3>关于我们</h3><p style="text-indent:2em">云资源是一个专注于资源共享的下载站点。</p>')
    custom_logo = db.Column(db.String(200), default='')
    hide_dev_info = db.Column(db.Boolean, default=False)
    custom_dev_info = db.Column(db.String(100), default='')
    hide_tutorial = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    @classmethod
    def get_config(cls):
        config = cls.query.first()
        if not config:
            config = cls(
                site_name='云资源',
                site_intro='欢迎来到云资源',
                about_content='<h3>关于我们</h3><p style="text-indent:2em">云资源是一个专注于资源共享的下载站点。</p>',
                custom_logo='',
                hide_dev_info=False,
                custom_dev_info='',
                hide_tutorial=False
            )
            db.session.add(config)
            db.session.commit()
            return config
        try:
            _ = config.custom_logo
            _ = config.hide_dev_info
            _ = config.custom_dev_info
        except Exception:
            try:
                db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN custom_logo VARCHAR(200) DEFAULT ''"))
            except:
                pass
            try:
                db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN hide_dev_info BOOLEAN DEFAULT 0"))
            except:
                pass
            try:
                db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN custom_dev_info VARCHAR(100) DEFAULT ''"))
            except:
                pass
            db.session.commit()
            config = cls.query.first()
        try:
            _ = config.hide_tutorial
        except Exception:
            try:
                db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN hide_tutorial BOOLEAN DEFAULT 0"))
                db.session.commit()
            except:
                pass
            config = cls.query.first()
        return config


class License(db.Model):
    __tablename__ = 'licenses'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.String(64), unique=True, nullable=False)
    license_type = db.Column(db.String(20), nullable=False, default='none')
    license_key = db.Column(db.String(256), nullable=False, default='')
    activated_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_valid = db.Column(db.Boolean, default=True)

    @classmethod
    def get_license(cls, machine_id):
        lic = cls.query.filter_by(machine_id=machine_id).first()
        if lic and lic.license_type in ['trial', 'annual'] and lic.expires_at:
            if datetime.now() > lic.expires_at:
                lic.is_valid = False
                db.session.commit()
        return lic


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    resources = db.relationship('Resource', backref='category', lazy=True)


class Tag(db.Model):
    __tablename__ = 'tags'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)


resource_tags = db.Table('resource_tags',
    db.Column('resource_id', db.Integer, db.ForeignKey('resources.id')),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'))
)


class Resource(db.Model):
    __tablename__ = 'resources'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    file_path = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(200))
    file_size = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    download_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    tags = db.relationship('Tag', secondary=resource_tags, lazy='subquery', backref=db.backref('resources', lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================================
# 初始化数据库 + 强制补齐缺失字段
# ============================================
with app.app_context():
    db.create_all()
    try:
        db.session.execute(db.text("PRAGMA journal_mode=WAL"))
        db.session.commit()
    except:
        pass

    # 强制补齐 SiteConfig 缺失字段（解决旧版升级 500 错误）
    try:
        db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN custom_logo VARCHAR(200) DEFAULT ''"))
    except:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN hide_dev_info BOOLEAN DEFAULT 0"))
    except:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN custom_dev_info VARCHAR(100) DEFAULT ''"))
    except:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE site_config ADD COLUMN hide_tutorial BOOLEAN DEFAULT 0"))
    except:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN download_categories TEXT DEFAULT ''"))
    except:
        pass
    try:
        db.session.commit()
    except:
        pass

    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', is_admin=True)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('默认管理员创建: admin / admin123')

    print('数据库初始化完成')
    print(f'数据目录: {TRIM_PKGVAR}')
    print(f'上传目录: {UPLOAD_FOLDER}')


# ============ 静态文件路由 ============
@app.route('/static/<path:filename>')
def static_file(filename):
    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp', '.tiff'}
    ext = os.path.splitext(filename)[1].lower()
    if ext in allowed_ext:
        return send_from_directory(os.path.join(TRIM_APPDEST, 'server'), filename)
    return "Not Found", 404


@app.route('/share/<path:filename>')
def share_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/<path:filename>')
def serve_file(filename):
    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp', '.tiff'}
    ext = os.path.splitext(filename)[1].lower()
    if ext in allowed_ext:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    return "Not Found", 404


# ============ 页面路由 ============

@app.route('/')
def index():
    config = SiteConfig.get_config()
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()
    return render_template('index.html', config=config, categories=categories, tags=tags)


@app.route('/category/<int:category_id>')
def category_detail(category_id):
    category = Category.query.get_or_404(category_id)
    config = SiteConfig.get_config()
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()

    query = Resource.query.filter_by(category_id=category_id)
    if current_user.is_authenticated and not current_user.is_admin:
        query = current_user.get_allowed_resource_query(query)
    resources = query.order_by(Resource.created_at.desc()).all()

    return render_template('category_detail.html',
                           category=category,
                           resources=resources,
                           categories=categories,
                           tags=tags,
                           config=config,
                           selected_category=category_id)


@app.route('/resources')
def resource_list():
    category_id = request.args.get('category', type=int)
    tag_id = request.args.get('tag', type=int)
    keyword = request.args.get('keyword', '').strip()

    query = Resource.query
    if category_id:
        query = query.filter_by(category_id=category_id)
    if tag_id:
        query = query.join(resource_tags).filter(resource_tags.c.tag_id == tag_id)
    if keyword:
        query = query.filter(Resource.title.contains(keyword) | Resource.description.contains(keyword))

    if current_user.is_authenticated and not current_user.is_admin:
        query = current_user.get_allowed_resource_query(query)

    resources = query.order_by(Resource.created_at.desc()).all()
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()
    config = SiteConfig.get_config()

    return render_template('resources.html', resources=resources, categories=categories, tags=tags, config=config,
                           selected_category=category_id, selected_tag=tag_id, keyword=keyword)


@app.route('/resource/<int:resource_id>')
def resource_detail(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    if current_user.is_authenticated and not current_user.is_admin:
        if not current_user.can_download_resource(resource):
            flash('您没有权限查看此资源', 'danger')
            return redirect(url_for('index'))

    config = SiteConfig.get_config()
    return render_template('resource_detail.html', resource=resource, config=config)


@app.route('/download/<int:resource_id>')
@login_required
def download_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    if not current_user.can_download_resource(resource):
        flash('您没有权限下载此资源', 'danger')
        return redirect(url_for('resource_detail', resource_id=resource_id))

    resource.download_count += 1
    db.session.commit()

    stored_filename = os.path.basename(resource.file_path)
    if '.' not in resource.file_name:
        if '.' in stored_filename:
            ext = stored_filename.rsplit('.', 1)[1]
            download_name = f"{resource.file_name}.{ext}"
        else:
            download_name = resource.file_name
    else:
        download_name = resource.file_name

    return send_from_directory(app.config['UPLOAD_FOLDER'], stored_filename, as_attachment=True, download_name=download_name)


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            login_user(user)
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        flash('用户名或密码错误', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ============ 管理后台 ============
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('需要管理员权限', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin')
@admin_required
def admin_dashboard():
    config = SiteConfig.get_config()
    resource_count = Resource.query.count()
    user_count = User.query.count()
    categories = Category.query.all()
    return render_template('admin_dashboard.html', config=config, resource_count=resource_count,
                           user_count=user_count, categories=categories)


# ---- 资源管理 ----
@app.route('/admin/resources')
@admin_required
def admin_resources():
    resources = Resource.query.order_by(Resource.created_at.desc()).all()
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()
    config = SiteConfig.get_config()
    return render_template('admin_resources.html', resources=resources, categories=categories, tags=tags, config=config)


@app.route('/admin/resources/add', methods=['GET'])
@admin_required
def admin_add_resource_page():
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()
    config = SiteConfig.get_config()
    return render_template('admin_add_resource.html', categories=categories, tags=tags, config=config)


@app.route('/admin/resources/add', methods=['POST'])
@admin_required
def admin_add_resource():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id', type=int)
    tag_ids = request.form.getlist('tag_ids')

    if not title:
        flash('请填写资源标题', 'danger')
        return redirect(url_for('admin_add_resource_page'))

    if 'file' not in request.files:
        flash('请选择文件', 'danger')
        return redirect(url_for('admin_add_resource_page'))

    file = request.files['file']
    if file.filename == '':
        flash('请选择文件', 'danger')
        return redirect(url_for('admin_add_resource_page'))

    original_filename = file.filename
    safe_display_name = get_safe_display_name(original_filename)

    safe_storage_name = secure_filename(original_filename)
    if not safe_storage_name:
        ext = ''
        if '.' in original_filename:
            ext = original_filename.rsplit('.', 1)[1]
            safe_storage_name = f'file.{ext}' if ext else 'file'
    file_path = get_unique_filepath(app.config['UPLOAD_FOLDER'], safe_storage_name)
    file.save(file_path)
    file_size = os.path.getsize(file_path)

    resource = Resource(
        title=title,
        description=description,
        file_path=file_path,
        file_name=safe_display_name,
        file_size=file_size,
        category_id=category_id
    )
    db.session.add(resource)

    if tag_ids:
        for tag_id in tag_ids:
            tag = Tag.query.get(tag_id)
            if tag:
                resource.tags.append(tag)
    db.session.commit()
    flash('资源添加成功', 'success')
    return redirect(url_for('admin_resources'))


# ============ 批量添加资源 ============
@app.route('/admin/resources/add/batch', methods=['POST'])
@admin_required
def admin_add_resources_batch():
    category_id = request.form.get('category_id', type=int)
    tag_ids = request.form.getlist('tag_ids')
    description = request.form.get('description', '').strip()
    title_prefix = request.form.get('title_prefix', '').strip()

    files = []
    if 'batch_files' in request.files:
        uploaded = request.files.getlist('batch_files')
        if uploaded and any(f.filename != '' for f in uploaded):
            files = uploaded

    if not files and 'folder_files' in request.files:
        uploaded = request.files.getlist('folder_files')
        if uploaded and any(f.filename != '' for f in uploaded):
            files = uploaded

    if not files:
        flash('请选择文件', 'danger')
        return redirect(url_for('admin_add_resource_page'))

    success_count = 0
    failed_files = []

    for file in files:
        if file.filename == '':
            continue
        try:
            original_filename = file.filename
            safe_display_name = get_safe_display_name(original_filename)

            safe_storage_name = secure_filename(original_filename)
            if not safe_storage_name:
                ext = ''
                if '.' in original_filename:
                    ext = original_filename.rsplit('.', 1)[1]
                    safe_storage_name = f'file.{ext}' if ext else 'file'
            file_path = get_unique_filepath(app.config['UPLOAD_FOLDER'], safe_storage_name)
            file.save(file_path)
            file_size = os.path.getsize(file_path)

            name_without_ext = os.path.splitext(original_filename)[0]
            title = f"{title_prefix}{name_without_ext}" if title_prefix else name_without_ext
            if not title:
                title = safe_display_name

            resource = Resource(
                title=title,
                description=description,
                file_path=file_path,
                file_name=safe_display_name,
                file_size=file_size,
                category_id=category_id
            )
            if tag_ids:
                for tag_id in tag_ids:
                    tag = Tag.query.get(tag_id)
                    if tag:
                        resource.tags.append(tag)
            db.session.add(resource)
            success_count += 1
        except Exception as e:
            failed_files.append(original_filename)
            print(f'批量上传失败: {original_filename}, 错误: {e}')

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'数据库提交失败: {str(e)}', 'danger')
        return redirect(url_for('admin_resources'))

    if success_count > 0:
        msg = f'成功添加 {success_count} 个资源'
        if failed_files:
            msg += f'，失败: {", ".join(failed_files[:5])}'
            if len(failed_files) > 5:
                msg += f' 等共 {len(failed_files)} 个'
        flash(msg, 'success' if not failed_files else 'warning')
    else:
        flash('没有成功添加任何资源，请检查文件是否有效', 'danger')

    return redirect(url_for('admin_resources'))


# ============ 替换资源文件（1.1.0 新增） ============
@app.route('/admin/resources/replace/<int:resource_id>', methods=['POST'])
@admin_required
def admin_replace_file(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    if 'file' not in request.files:
        flash('请选择文件', 'danger')
        return redirect(url_for('admin_edit_resource_page', resource_id=resource_id))

    file = request.files['file']
    if file.filename == '':
        flash('请选择文件', 'danger')
        return redirect(url_for('admin_edit_resource_page', resource_id=resource_id))

    original_filename = file.filename
    safe_display_name = get_safe_display_name(original_filename)

    if os.path.exists(resource.file_path):
        try:
            os.remove(resource.file_path)
        except Exception as e:
            print(f'删除旧文件失败: {e}')

    safe_storage_name = secure_filename(original_filename)
    if not safe_storage_name:
        ext = ''
        if '.' in original_filename:
            ext = original_filename.rsplit('.', 1)[1]
            safe_storage_name = f'file.{ext}' if ext else 'file'
    file_path = get_unique_filepath(app.config['UPLOAD_FOLDER'], safe_storage_name)
    file.save(file_path)
    file_size = os.path.getsize(file_path)

    resource.file_path = file_path
    resource.file_name = safe_display_name
    resource.file_size = file_size
    resource.updated_at = datetime.now()
    db.session.commit()

    flash('文件替换成功', 'success')
    return redirect(url_for('admin_edit_resource_page', resource_id=resource_id))


@app.route('/admin/resources/edit/<int:resource_id>', methods=['GET'])
@admin_required
def admin_edit_resource_page(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    categories = Category.query.order_by(Category.sort_order).all()
    tags = Tag.query.all()
    config = SiteConfig.get_config()
    return render_template('admin_resource_edit.html', resource=resource, categories=categories, tags=tags, config=config)


@app.route('/admin/resources/edit/<int:resource_id>', methods=['POST'])
@admin_required
def admin_edit_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id', type=int)
    tag_ids = request.form.getlist('tag_ids')

    if not title:
        flash('请填写资源标题', 'danger')
        return redirect(url_for('admin_edit_resource_page', resource_id=resource_id))

    resource.title = title
    resource.description = description
    resource.category_id = category_id if category_id else None
    resource.tags = []
    if tag_ids:
        for tag_id in tag_ids:
            tag = Tag.query.get(tag_id)
            if tag:
                resource.tags.append(tag)

    db.session.commit()
    flash('资源已更新', 'success')
    return redirect(url_for('admin_edit_resource_page', resource_id=resource_id))


@app.route('/admin/resources/delete/<int:resource_id>', methods=['POST'])
@admin_required
def admin_delete_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    try:
        if os.path.exists(resource.file_path):
            os.remove(resource.file_path)
    except:
        pass
    db.session.delete(resource)
    db.session.commit()
    flash('资源已删除', 'success')
    return redirect(url_for('admin_resources'))


# ---- 分类管理 ----
@app.route('/admin/categories', methods=['POST'])
@admin_required
def admin_add_category():
    name = request.form.get('name', '').strip()
    if name and not Category.query.filter_by(name=name).first():
        db.session.add(Category(name=name))
        db.session.commit()
        flash('分类添加成功', 'success')
    else:
        flash('分类已存在', 'warning')
    return redirect(url_for('admin_resources'))


@app.route('/admin/categories/delete/<int:category_id>', methods=['POST'])
@admin_required
def admin_delete_category(category_id):
    category = Category.query.get_or_404(category_id)
    db.session.delete(category)
    db.session.commit()
    flash('分类已删除', 'success')
    return redirect(url_for('admin_resources'))


# ---- 标签管理 ----
@app.route('/admin/tags', methods=['POST'])
@admin_required
def admin_add_tag():
    name = request.form.get('name', '').strip()
    if name and not Tag.query.filter_by(name=name).first():
        db.session.add(Tag(name=name))
        db.session.commit()
        flash('标签添加成功', 'success')
    else:
        flash('标签已存在', 'warning')
    return redirect(url_for('admin_resources'))


@app.route('/admin/tags/delete/<int:tag_id>', methods=['POST'])
@admin_required
def admin_delete_tag(tag_id):
    tag = Tag.query.get_or_404(tag_id)
    db.session.delete(tag)
    db.session.commit()
    flash('标签已删除', 'success')
    return redirect(url_for('admin_resources'))


# ---- 用户管理 ----
@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.all()
    config = SiteConfig.get_config()
    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('admin_users.html', users=users, config=config,
                           categories=categories, tags=tags,
                           is_app_activated=is_app_activated())


@app.route('/admin/users/add', methods=['POST'])
@admin_required
def admin_add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    is_admin = request.form.get('is_admin') == 'on'

    if not username or len(username) < 2:
        flash('用户名至少2位', 'danger')
        return redirect(url_for('admin_users'))
    if not password or len(password) < 6:
        flash('密码至少6位', 'danger')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(username=username).first():
        flash('用户名已存在', 'danger')
        return redirect(url_for('admin_users'))

    user = User(username=username, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'用户 {username} 添加成功', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/edit/<int:user_id>', methods=['GET'])
@admin_required
def admin_edit_user_page(user_id):
    user = User.query.get_or_404(user_id)
    config = SiteConfig.get_config()
    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('admin_user_edit.html', user=user, config=config,
                           categories=categories, tags=tags,
                           is_app_activated=is_app_activated())


@app.route('/admin/users/edit/<int:user_id>', methods=['POST'])
@admin_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if user_id == current_user.id:
        flash('不能修改自己的权限', 'danger')
        return redirect(url_for('admin_users'))

    if not is_app_activated():
        flash('应用未激活，无法修改权限', 'danger')
        return redirect(url_for('admin_users'))

    download_categories = request.form.get('download_categories', '').strip()
    user.download_categories = download_categories
    db.session.commit()

    flash(f'用户 {user.username} 的权限已更新', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash('不能删除自己', 'danger')
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('用户已删除', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/toggle/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle_user(user_id):
    if user_id == current_user.id:
        flash('不能禁用自己', 'danger')
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    status = '启用' if user.is_active else '禁用'
    flash(f'用户已{status}', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/reset-password/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    if user_id == current_user.id:
        flash('不能重置自己的密码，请使用「修改密码」功能', 'warning')
        return redirect(url_for('admin_users'))

    user = User.query.get_or_404(user_id)
    user.set_password('123456')
    db.session.commit()
    flash(f'用户密码已重置为: 123456', 'success')
    return redirect(url_for('admin_users'))


# ---- 站点设置 ----
@app.route('/admin/settings')
@admin_required
def admin_settings():
    config = SiteConfig.get_config()
    license_info = None
    machine_id = get_machine_id()
    if machine_id:
        license_info = License.get_license(machine_id)
    return render_template('admin_settings.html', config=config, license=license_info)


@app.route('/admin/settings', methods=['POST'])
@admin_required
def admin_save_settings():
    config = SiteConfig.get_config()

    site_name = request.form.get('site_name', '').strip()
    site_intro = request.form.get('site_intro', '').strip()
    about_content = request.form.get('about_content', '').strip()

    if site_name:
        config.site_name = site_name
    if site_intro:
        config.site_intro = site_intro
    if about_content:
        config.about_content = about_content

    machine_id = get_machine_id()
    license_info = None
    if machine_id:
        license_info = License.get_license(machine_id)

    if license_info and license_info.is_valid and license_info.license_type != 'none':
        config.hide_dev_info = request.form.get('hide_dev_info') == 'on'
        config.custom_dev_info = request.form.get('custom_dev_info', '').strip()
        config.hide_tutorial = request.form.get('hide_tutorial') == 'on'

        if 'custom_logo' in request.files:
            file = request.files['custom_logo']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                if ext in ['png', 'jpg', 'jpeg', 'svg']:
                    logo_filename = f'logo_{datetime.now().strftime("%Y%m%d%H%M%S")}.{ext}'
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], logo_filename)
                    file.save(file_path)
                    if config.custom_logo and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], config.custom_logo)):
                        try:
                            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], config.custom_logo))
                        except:
                            pass
                    config.custom_logo = logo_filename

    db.session.commit()
    flash('站点设置已保存', 'success')
    return redirect(url_for('admin_settings'))


# ---- 修改密码 ----
@app.route('/admin/change-password', methods=['POST'])
@admin_required
def admin_change_password():
    new_password = request.form.get('new_password', '').strip()
    if not new_password or len(new_password) < 6:
        flash('密码至少6位', 'danger')
        return redirect(url_for('admin_dashboard'))

    user = User.query.get(current_user.id)
    user.set_password(new_password)
    db.session.commit()
    flash('密码修改成功', 'success')
    return redirect(url_for('admin_dashboard'))


# ---- 授权管理 ----
@app.route('/admin/license')
@admin_required
def admin_license():
    config = SiteConfig.get_config()
    machine_id = get_machine_id()
    license_info = None
    if machine_id:
        license_info = License.get_license(machine_id)
    return render_template('admin_license.html', config=config, license=license_info, machine_id=machine_id)


@app.route('/admin/license/activate', methods=['POST'])
@admin_required
def admin_activate_license():
    machine_id = get_machine_id()
    if not machine_id:
        flash('无法读取设备机器码', 'danger')
        return redirect(url_for('admin_license'))

    license_code = request.form.get('activation_code', '').strip()
    if not license_code:
        flash('请输入激活码', 'warning')
        return redirect(url_for('admin_license'))

    valid, result = verify_license_code('cloud-resources', SECRET_KEY, license_code, machine_id)

    if not valid:
        flash(f'{result}', 'danger')
        return redirect(url_for('admin_license'))

    license_type = 'lifetime'
    expires_at = None
    if result.get('type') == 'enhanced':
        data = result.get('data', {})
        expire = data.get('expire', 'permanent')
        if expire != 'permanent':
            try:
                expire_date = datetime.strptime(expire, '%Y-%m-%d')
                days = (expire_date - datetime.now()).days
                if days <= 7:
                    license_type = 'trial'
                    expires_at = datetime.now() + timedelta(days=7)
                elif days <= 365:
                    license_type = 'annual'
                    expires_at = datetime.now() + timedelta(days=365)
                else:
                    license_type = 'lifetime'
            except:
                license_type = 'lifetime'
    else:
        license_type = 'lifetime'

    existing = License.query.filter_by(machine_id=machine_id).first()
    if existing:
        existing.license_type = license_type
        existing.license_key = license_code
        existing.expires_at = expires_at
        existing.is_valid = True
        existing.activated_at = datetime.now()
    else:
        new_license = License(
            machine_id=machine_id,
            license_type=license_type,
            license_key=license_code,
            expires_at=expires_at,
            is_valid=True
        )
        db.session.add(new_license)
    db.session.commit()

    type_names = {'trial': '体验套餐（7天）', 'annual': '年套餐（365天）', 'lifetime': '终身套餐'}
    flash(f'激活成功！当前套餐：{type_names.get(license_type, license_type)}', 'success')
    return redirect(url_for('admin_license'))


# ============ API ============
@app.route('/api/tags')
def api_tags():
    tags = Tag.query.all()
    return jsonify([{'id': t.id, 'name': t.name} for t in tags])


@app.route('/api/resource/<int:resource_id>')
@admin_required
def api_get_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    return jsonify({
        'id': resource.id,
        'title': resource.title,
        'description': resource.description or '',
        'category_id': resource.category_id,
        'tag_ids': [t.id for t in resource.tags]
    })


# ============ 启动 ============
if __name__ == '__main__':
    port = int(os.environ.get('TRIM_SERVICE_PORT', 5665))
    print(f'启动云资源...')
    print(f'数据目录: {TRIM_PKGVAR}')
    print(f'上传目录: {UPLOAD_FOLDER}')
    print(f'http://0.0.0.0:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)