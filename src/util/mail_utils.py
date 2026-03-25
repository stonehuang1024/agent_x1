import os
import sys
import smtplib
import traceback
import mimetypes
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email import encoders

# 发件人邮箱和密码
MAIL_HOST = 'smtp.qq.com'
MAIL_PORT = 465
MAIL_USER = 'shudong.huang@qq.com'
MAIL_PWD = ''

logger = logging.getLogger(__name__)


class MailUtils(object):
    MAIL_RECV_LIST = ['shudong.huang@qq.com', '317144468@qq.com']

    @classmethod
    def send_mail(cls, recv_list, subject, content, subtype='plain', att_list=None, embed_images=None):
        """
        发送邮件
        :param recv_list: 收件人列表
        :param subject: 邮件主题
        :param content: 邮件正文
        :param subtype: 邮件正文类型（plain 或 html）
        :param att_list: 附件文件路径列表
        :param embed_images: 嵌入图片的路径列表（仅当 subtype='html' 时有效）
        :return: True if success, False otherwise
        """
        # 创建邮件对象
        message = MIMEMultipart('related')
        message['From'] = MAIL_USER
        message['To'] = ','.join(recv_list)
        message['Subject'] = subject

        # 添加邮件正文
        if subtype.lower() == 'html' and embed_images:
            # 如果是 HTML 邮件且需要嵌入图片，先添加正文
            msg_text = MIMEText(content, subtype.lower(), 'utf-8')
            message.attach(msg_text)
            # 然后添加嵌入图片
            for i, image_path in enumerate(embed_images):
                if os.path.exists(image_path):
                    with open(image_path, 'rb') as img_file:
                        img = MIMEImage(img_file.read())
                        img.add_header('Content-ID', f'<image{i}>')
                        img.add_header('Content-Disposition', 'inline', filename=os.path.basename(image_path))
                        message.attach(img)
        else:
            msg_text = MIMEText(content, subtype.lower(), 'utf-8')
            message.attach(msg_text)

        # 添加附件
        if att_list is not None:
            for att in att_list:
                if os.path.exists(att):
                    logger.info(f"添加附件: {att}")
                    message.attach(cls.get_attachment(att))

        try:
            # 连接 SMTP 服务器并发送邮件
            server = smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT)
            server.ehlo()
            server.login(MAIL_USER, MAIL_PWD)
            server.sendmail(MAIL_USER, recv_list, message.as_string())
            server.close()
            logger.info(f"邮件发送成功: {recv_list}")
            return True
        except Exception as ex:
            exc_msg = traceback.format_exc()
            logger.error(f'错误: 无法发送邮件, {exc_msg}')
            return False

    @classmethod
    def get_attachment(cls, att_file_path):
        """读取未知文件类型附件"""
        content_type, encoding = mimetypes.guess_type(att_file_path)
        if content_type is None or encoding is not None:
            content_type = 'application/octet-stream'
        main_type, sub_type = content_type.split('/', 1)

        att = None
        with open(att_file_path, 'rb') as inf:
            if main_type == 'text':
                att = MIMEText(inf.read().decode('utf-8'), _subtype=sub_type)
            elif main_type == 'image':
                att = MIMEImage(inf.read(), _subtype=sub_type)
            else:
                att = MIMEBase(main_type, sub_type)
                att.set_payload(inf.read())
                encoders.encode_base64(att)
        att.add_header('Content-Disposition', 'attachment', filename=os.path.basename(att_file_path))
        return att

    @classmethod
    def df_to_html(cls, df):
        # 添加表头样式（浅蓝色背景和加粗）
        html = df.to_html(index=True, border=1)
        html = html.replace('<th>', '<th style="background-color: lightblue; font-weight: bold;">')
        return html

    ## 格式为： [[title, df], [title, df]]
    @classmethod
    def create_email_content(cls, df_list):
        html_content = """
        <html>
            <body>
                <h2>DataFrame 表格</h2>
        """
        for i, df_infos in enumerate(df_list):
            df_name, df = df_infos
            html_content += f"<h3>表格{i + 1}: {df_name}</h3>"
            html_content += MailUtils.df_to_html(df)
        html_content += """
            </body>
        </html>
        """
        return html_content

if __name__ == '__main__':
    # HTML 邮件正文，嵌入图片
    html_content = """
    <html>
        <body>
            <h1>这是一封测试邮件</h1>
            <p>邮件正文中嵌入了图片：</p>
            <img src="cid:image0" alt="嵌入图片">
        </body>
    </html>
    """

    # 发送测试邮件，嵌入图片到正文
    MailUtils.send_mail(
        recv_list=['shudong.huang@qq.com'],
        subject='测试邮件 - 嵌入图片',
        content=html_content,
        subtype='html',
        embed_images=None
    )
