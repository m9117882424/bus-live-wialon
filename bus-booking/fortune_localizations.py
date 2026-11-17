from fortunes import _LEGACY, _OPENINGS, _ENDINGS
_LEGACY_KK = [
'Бүгін сәттілік сізбен бірге!', 'Сізді жақсы күн күтеді!', 'Жақсы жаңалықтар жолда!', 'Жолыңыз жеңіл болсын!',
'Күлкі жаңа мүмкіндіктер ашады!', 'Қуанышты оқиғалар жақын!', 'Бүгін жаңа ойларға тамаша күн!', 'Бәрі ойдағыдай болсын!']
_LEGACY_TK = [
'Bu gün şowlulyk siziň bilen!', 'Sizi ajaýyp gün garaşýar!', 'Hoş habarlar ýolda!', 'Ýoluňyz açyk bolsun!',
'Ýylgyryş täze mümkinçilikleri açar!', 'Gowy wakalar golaýda!', 'Bu gün täze pikirler üçin ajaýyp gün!', 'Hemme zat göwnüňizçe bolsun!']
_OPEN_KK = [
'Бүгінгі күн сізге қуаныш сыйласын!', 'Жол сізге жақсы көңіл күй сыйласын!', 'Бүгін бәрі ойдағыдай болсын!', 'Сізге тамаша күн тілейміз!',
'Сәттілік жаныңызда болсын!', 'Күніңіз күлкімен басталсын!', 'Бүгін қуануға себеп табылсын!', 'Сапарыңыз тыныш өтсін!',
'Алда жақсы күндер көп болсын!', 'Бұл күн сізді жағымды таңғалдырсын!', 'Сізге жеңіл жол тілейміз!', 'Жақсы көңіл күй сізбен бірге болсын!',
'Бұл күн жақсы өтсін!', 'Бүгін жолыңыз болсын!', 'Әр сағат жақсылық әкелсін!', 'Сізге тыныштық пен күш тілейміз!',
'Күніңіз жағымды сәттер сыйласын!', 'Жоспарларыңыз оңай орындалсын!', 'Көбірек күлімдеуге себеп тілейміз!', 'Бүгін бәрі сәтті болсын!',
'Жолыңыз жайлы әрі тыныш болсын!', 'Бұл күн жақсы әсер қалдырсын!', 'Сізге шабыт тілейміз!', 'Жаныңызда жақсы адамдар болсын!',
'Күніңіз жаңа мүмкіндіктер әкелсін!', 'Сапарыңыз жақсы өтсін!', 'Сізге жарқын көңіл күй тілейміз!', 'Ойлағаныңыз орындалсын!',
'Бүгін күлкі көп болсын!', 'Күніңіз жеңіл басталсын!', 'Әр ісіңізге сәттілік тілейміз!', 'Бүгін бір жағымды оқиға болсын!']
_OPEN_TK = [
'Bu gün size şatlyk getirsin!', 'Ýol size gowy keýp paýlasyn!', 'Bu gün ähli işiňiz ugruna bolsun!', 'Size ajaýyp gün arzuw edýäris!',
'Şowlulyk ýanyňyzda bolsun!', 'Günüňiz ýylgyryş bilen başlansyn!', 'Bu gün şatlanmaga sebäp tapylsyn!', 'Saparyňyz rahat geçsin!',
'Öňüňizde köp gowulyk bolsun!', 'Bu gün sizi hoşal galdyrsyn!', 'Size ak ýol arzuw edýäris!', 'Gowy keýp size ýaran bolsun!',
'Bu günüňiz gowy geçsin!', 'Bu gün şowlulyk size ýaran bolsun!', 'Her sagat gowulyk getirsin!', 'Size rahatlyk we güýç arzuw edýäris!',
'Günüňiz ýakymly pursatlary getirsin!', 'Meýilnamalaryňyz aňsat amala aşsyn!', 'Size ýylgyrmaga has köp sebäp arzuw edýäris!', 'Bu gün ähli işiňiz şowly bolsun!',
'Ýoluňyz amatly we rahat bolsun!', 'Bu gün gowy täsir galdyrsyn!', 'Size ylham arzuw edýäris!', 'Ýanyňyzda gowy adamlar bolsun!',
'Günüňiz täze mümkinçilikler getirsin!', 'Saparyňyz ýakymly geçsin!', 'Size şadyýan keýp arzuw edýäris!', 'Ähli niýetleriňiz hasyl bolsun!',
'Bu gün ýylgyryşlar köp bolsun!', 'Günüňiz ýeňil başlansyn!', 'Her işiňizde size üstünlik arzuw edýäris!', 'Bu gün ýakymly bir zat bolsun!']
_END_KK = [
'Алда жағымды кездесулер күтеді.', 'Әрқашан қолдау жаныңызда болсын.', 'Жақсы жаңалықтар сізге жетсін.', 'Демалуға уақыт табылсын.',
'Жақсы оқиғалар көбейсін.', 'Күлкі бұл күнді жылыта түссін.', 'Маңызды істеріңіз уақытында бітсін.', 'Жолыңыз жайлы болсын.',
'Әр күн жаңа мүмкіндіктер әкеледі.', 'Жақындарыңыз көңіл бөліп қуантсын.', 'Жаныңызда жақсы сұхбаттас болсын.', 'Кішкентай қуаныштар жиі кездессін.',
'Армандарыңыз біртіндеп орындалсын.', 'Күніңіз жағымды кешпен аяқталсын.', 'Жұмысыңыз қанағат әкелсін.', 'Әр жаңа қадамыңыз қуантсын.',
'Сүйікті істеріңізге уақыт табылсын.', 'Жақсы жаңалықтар көп күттірмесін.', 'Бұл күн сізге қуат сыйласын.', 'Үйде сізді жылылық пен жайлылық күтсін.',
'Барлық кездесулеріңіз жақсы өтсін.', 'Бүгін күлімдеуге уақыт табылсын.', 'Еңбегіңіз жақсы нәтиже берсін.', 'Жаныңызда әрқашан сенімді достар болсын.',
'Бұл күн жақсы сәтімен есте қалсын.', 'Тыныштық пен сенімділік серік болсын.', 'Жоспарларыңыз қуантсын, істеріңіз алға бассын.', 'Сәтті ойлар оңай келсін.',
'Ойлағанның бәріне күшіңіз жетсін.', 'Күніңіз жарық болсын.', 'Алғыс айтуға себеп көбейсін.']
_END_TK = [
'Öňüňizde ýakymly duşuşyklar bar.', 'Goldaw elmydama ýanyňyzda bolsun.', 'Hoş habarlar size ýetip gelsin.', 'Dynç almaga wagt tapylsyn.',
'Gowy wakalar köpelsin.', 'Ýylgyryş şu güni ýylytsyn.', 'Möhüm işler wagtynda bitsin.', 'Ýoluňyz amatly geçsin.',
'Her gün täze mümkinçilikler getirýär.', 'Ýakynlaryňyz üns berip begendirsin.', 'Ýanyňyzda gowy söhbetdeş bolsun.', 'Kiçijik şatlyklar ýygy duşsun.',
'Arzuwlaryňyz ädimme-ädim hasyl bolsun.', 'Günüňiz ýakymly agşam bilen tamamlansyn.', 'Işiňiz göwnüňizden tursun.', 'Her täze ädimiňiz sizi begendirsin.',
'Halaýan işleriňize wagt tapylsyn.', 'Hoş habarlar özüni garaşdyrmasyn.', 'Bu gün size güýç-kuwwat bersin.', 'Öýde sizi ýylylyk we rahatlyk garşylasyn.',
'Ähli duşuşyklaryňyz gowy geçsin.', 'Bu gün ýylgyrmaga wagt tapylsyn.', 'Zähmetiňiz gowy netije bersin.', 'Ýanyňyzda ygtybarly dostlar bolsun.',
'Bu gün gowy zat bilen ýatda galsyn.', 'Rahatlyk we ynam size ýaran bolsun.', 'Meýilnamalaryňyz begendirsin, işleriňiz ugruna bolsun.', 'Gowy pikirler aňsat gelsin.',
'Ähli niýetleriňize güýjüňiz ýetsin.', 'Günüňiz ýagty bolsun.', 'Şükür etmäge sebäp köpelsin.']
_LEGACY_ZH = [
'今天好运与您同行！', '美好的一天在等着您！', '好消息已经在路上！', '愿您一路顺畅！',
'微笑将为您开启新的机会！', '喜事就在身边！', '今天是萌发新想法的好日子！', '愿一切都如您所愿！']
_OPEN_ZH = [
'愿今天为您带来快乐！', '愿旅途带给您好心情！', '愿您今天事事顺心！', '祝您度过美好的一天！',
'愿好运常伴您左右！', '愿您的一天从微笑开始！', '愿您今天找到开心的理由！', '愿您的旅途平安宁静！',
'愿前方有许多美好等着您！', '愿今天给您一份惊喜！', '祝您一路轻松！', '愿好心情一路相伴！',
'愿您今天过得温暖美好！', '愿您今天好运相随！', '愿每个小时都有好事发生！', '愿您内心平静、充满力量！',
'愿今天为您留下愉快的时光！', '愿您的计划轻松实现！', '愿您拥有更多微笑的理由！', '愿您今天一切顺利！',
'愿您的旅途舒适安稳！', '愿今天为您留下美好的印象！', '愿您灵感不断！', '愿您身边都是善良的人！',
'愿今天为您带来新的机会！', '愿您旅途愉快！', '愿您的心情如阳光般明朗！', '愿您心中所想都能实现！',
'愿今天多一些笑容！', '愿您轻松开启新的一天！', '祝您做每件事都顺利！', '愿今天有美好的事情发生！']
_END_ZH = [
'前方有愉快的相遇在等着您。', '愿您身边总有人支持。', '好消息会来到您身边。', '愿您有时间好好休息。',
'愿美好的事情越来越多。', '一个微笑会让今天更温暖。', '愿重要的事情都能按时完成。', '愿您一路舒适自在。',
'每一天都带来新的机会。', '愿亲人的关心让您开心。', '愿您遇到聊得来的旅伴。', '愿生活中的小快乐常常出现。',
'愿您心中的计划一步步成为现实。', '愿您以一个愉快的夜晚结束今天。', '愿工作带给您满足感。', '愿每一次前进都让您欣喜。',
'愿您有时间做喜欢的事。', '愿好消息早日到来。', '愿今天让您充满活力。', '愿家中的温暖与舒适等着您。',
'愿每一次会面都顺利愉快。', '愿您今天有片刻微笑的闲暇。', '愿您的努力收获好成果。', '愿可靠的朋友常伴身边。',
'愿今天因一件好事而值得回味。', '愿平静与自信伴您同行。', '愿计划令人期待，事情进展顺利。', '愿好点子轻松涌现。',
'愿您有足够的精力完成心中所想。', '愿您的一天多一些明亮。', '愿值得感恩的事越来越多。']
assert len(_LEGACY_KK)==len(_LEGACY_TK)==len(_LEGACY_ZH)==len(_LEGACY)
assert len(_OPEN_KK)==len(_OPEN_TK)==len(_OPEN_ZH)==len(_OPENINGS)
assert len(_END_KK)==len(_END_TK)==len(_END_ZH)==len(_ENDINGS)
_TRANSLATIONS = {
 'kk': {**{pair[0]: v for pair,v in zip(_LEGACY,_LEGACY_KK)}, **{a[0]+' '+b[0]: x+' '+y for a,x in zip(_OPENINGS,_OPEN_KK) for b,y in zip(_ENDINGS,_END_KK)}},
 'tk': {**{pair[0]: v for pair,v in zip(_LEGACY,_LEGACY_TK)}, **{a[0]+' '+b[0]: x+' '+y for a,x in zip(_OPENINGS,_OPEN_TK) for b,y in zip(_ENDINGS,_END_TK)}},
 'zh': {**{pair[0]: v for pair,v in zip(_LEGACY,_LEGACY_ZH)}, **{a[0]+' '+b[0]: x+' '+y for a,x in zip(_OPENINGS,_OPEN_ZH) for b,y in zip(_ENDINGS,_END_ZH)}},
}
def fortune_localized(value, lang):
    return _TRANSLATIONS.get(lang, {}).get(value, value)
