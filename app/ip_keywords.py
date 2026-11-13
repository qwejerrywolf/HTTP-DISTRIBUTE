# Shared by highlighting and the optional automatic blocking policy.
IP_FLAG_KEYWORDS = tuple(word.casefold() for word in (
    'IDC', '数据中心', '数据中心节点', '云服务器', '云计算', '云主机', '云服务',
    '服务器托管', '主机托管', '机房', 'IDC机房', 'BGP机房', 'BGP节点', '弹性计算',
    'datacenter', 'data center', 'hosting', 'hosted', 'cloud computing', 'cloud hosting',
    'cloud server', 'virtual private server', 'vps', 'colocation', 'co-location',
    'dedicated server', 'server hosting', 'compute instance',
    '阿里云', '阿里巴巴', 'Alibaba Cloud', 'Aliyun', '腾讯云', 'Tencent Cloud',
    'Tencent Computing', '华为云', 'Huawei Cloud', '天翼云', 'CTYUN', '移动云',
    'Ecloud', '联通云', '沃云', '百度智能云', 'Baidu Cloud', '火山引擎',
    'Volcengine', '京东云', 'JD Cloud', '金山云', 'Kingsoft Cloud', '优刻得',
    'UCloud', '青云', 'QingCloud', '浪潮云', 'Inspur Cloud',
    'Amazon Web Services', 'Amazon Technologies', 'AWS', 'Microsoft Azure',
    'Microsoft Corporation', 'Google Cloud', 'Google LLC', 'Oracle Cloud',
    'Oracle Corporation', 'DigitalOcean', 'Vultr', 'Linode', 'Akamai Connected Cloud',
    'Hetzner', 'OVH', 'OVHcloud', 'Contabo', 'Leaseweb', 'Scaleway', 'IONOS',
    'Rackspace', 'Cloudflare', 'Akamai', 'Fastly',
    'BGP', '骨干网', '专线', '企业网络', '网络科技', '信息技术', '通信技术',
    '互联网服务', 'CDN', '边缘节点', 'edge', 'network', 'internet services',
    'infrastructure', 'telecom', 'broadband', 'proxy', 'vpn',
))


def flagged(location):
    text = location.casefold()
    return any(keyword in text for keyword in IP_FLAG_KEYWORDS)
