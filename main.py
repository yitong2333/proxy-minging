import re
import os
import yaml
import threading
import base64
import requests
from loguru import logger
from tqdm import tqdm
from retry import retry
from urllib.parse import unquote
from retrying import retry

from pre_check import pre_check

new_sub_list = []
new_clash_list = []
new_v2_list = []

@logger.catch
def yaml_check(path_yaml):
    print(os.path.isfile(path_yaml))
    if os.path.isfile(path_yaml): #存在，非第一次
        with open(path_yaml,encoding="UTF-8") as f:
            dict_url = yaml.load(f, Loader=yaml.FullLoader)
    else:
        dict_url = {
            "机场订阅":[],
            "clash订阅":[],
            "v2订阅":[]
        }
    # with open(path_yaml, 'w',encoding="utf-8") as f:
    #     data = yaml.dump(dict_url, f,allow_unicode=True)
    logger.info('读取文件成功')
    return dict_url

@logger.catch
def get_config():
    with open('./config.yaml',encoding="UTF-8") as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
    list_tg = data['tgchannel']
    new_list = []
    for url in list_tg:
        a = url.split("/")[-1]
        url = 'https://t.me/s/'+a
        new_list.append(url)
    return new_list

@logger.catch
@retry(stop_max_attempt_number=10,stop_max_delay=1000)
def get_channel_http(channel_url):
    try:
        with requests.post(channel_url) as resp:
            data = resp.text
        url_list = re.findall("https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]", data)  # 使用正则表达式查找订阅链接并创建列表
        text_list = re.findall("vmess://[^\s<]+|vless://[^\s<]+|ss://[^\s<]+|ssr://[^\s<]+|trojan://[^\s<]+", data)
        print(text_list)
        logger.info(channel_url+'\t获取成功')
    except Exception as e:
        logger.warning(channel_url+'\t获取失败')
        logger.error(channel_url+e)
        url_list = []
        text_list = []
    finally:
        return url_list, text_list

def filter_base64(text):
    ss = ['ss://','ssr://','vmess://','trojan://','vless://']
    for i in ss:
        if i in text:
            return True
    return False


@logger.catch
def sub_check(url,bar):
    headers = {'User-Agent': 'ClashforWindows/0.18.1'}
    with thread_max_num:
        @retry(tries=2)
        def start_check(url):
            res=requests.get(url,headers=headers,timeout=5)#设置5秒超时防止卡死
            if res.status_code == 200:
                try: #有流量信息
                    info = res.headers['subscription-userinfo']
                    info_num = re.findall('\d+',info)
                    new_sub_list.append(url)
                except:
                    # 判断是否为clash
                    try:
                        u = re.findall('proxies:', res.text)[0]
                        if u == "proxies:":
                            new_clash_list.append(url)
                    except:
                        # 判断是否为v2
                        try:
                            # 解密base64
                            text = res.text[:64]
                            text = base64.b64decode(text)
                            text = str(text)
                            if filter_base64(text):
                                new_v2_list.append(url)
                        # 均不是则非订阅链接
                        except:
                            pass
            else:
                pass
        try:
            start_check(url)
        except:
            pass
        bar.update(1)

if __name__=='__main__':
    path_yaml = pre_check()
    dict_url = yaml_check(path_yaml)
    # print(dict_url)
    list_tg = get_config()
    logger.info('读取config成功')
    #循环获取频道订阅
    url_list = []
    proxy_list = []
    for channel_url in list_tg:
        temp_url_list, temp_text_list = get_channel_http(channel_url)
        url_list.extend(temp_url_list)
        proxy_list.extend(temp_text_list)
    logger.info('开始筛选---')
    thread_max_num = threading.Semaphore(32)  # 32线程
    bar = tqdm(total=len(url_list), desc='订阅筛选：')
    thread_list = []
    for url in url_list:
        # 为每个新URL创建线程
        t = threading.Thread(target=sub_check, args=(url, bar))
        # 加入线程池并启动
        thread_list.append(t)
        t.setDaemon(True)
        t.start()
    for t in thread_list:
        t.join()
    bar.close()
    logger.info('筛选完成')
    old_sub_list = dict_url['机场订阅']
    old_clash_list = dict_url['clash订阅']
    old_v2_list = dict_url['v2订阅']
    new_sub_list.extend(old_sub_list)
    new_clash_list.extend(old_clash_list)
    new_v2_list.extend(old_v2_list)
    new_sub_list = sorted(set(new_sub_list))
    new_clash_list = sorted(set(new_clash_list))
    new_v2_list = sorted(set(new_v2_list))
    new_proxy_list = sorted(set(proxy_list))

    dict_url.update({'机场订阅':new_sub_list})
    dict_url.update({'clash订阅': new_clash_list})
    dict_url.update({'v2订阅': new_v2_list})
    with open(path_yaml, 'w',encoding="utf-8") as f:
        data = yaml.dump(dict_url, f,allow_unicode=True)

    with open('latest.yaml', 'w',encoding="utf-8") as f:
        yaml.dump(dict_url, f,allow_unicode=True)

    with open('v2ray.txt', 'w', encoding="utf-8") as f:
        for line in proxy_list:
            f.write(unquote(line))
            f.write('\n')
