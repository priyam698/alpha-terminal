
module.exports = async function handler(req, res) {
    // Enable complete CORS coverage
    res.setHeader('Access-Control-Allow-Credentials', true);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS,PATCH,DELETE,POST,PUT');
    res.setHeader('Access-Control-Allow-Headers', 'X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version');

    if (req.method === 'OPTIONS') {
        return res.status(200).end();
    }

    if (req.method !== 'POST') {
        return res.status(405).json({ error: 'Method Not Allowed' });
    }

    try {
        const apiKey = process.env.GROQ_API_KEY;
        if (!apiKey) {
            return res.status(500).json({ error: 'Configuration Error: GROQ_API_KEY is missing on Vercel environment variables.' });
        }

        // BULLETPROOF PARSER: Safely reads raw text chunks, strings, or pre-parsed json objects
        let requestBody = req.body;
        if (typeof requestBody === 'string') {
            try {
                requestBody = JSON.parse(requestBody);
            } catch (pErr) {
                // Handle unparsed multi-part forms if necessary
            }
        }

        if (!requestBody || (!requestBody.systemPrompt && !requestBody.userPrompt)) {
            return res.status(400).json({ error: 'Payload Error: Incoming parameters are blank or unparsed.' });
        }

        const systemPrompt = requestBody.systemPrompt || "You are a financial terminal assistant.";
        let userPrompt = requestBody.userPrompt || "";

        if (userPrompt.toLowerCase().includes("stock ticker:")) {
            let rawTicker = userPrompt.substring(userPrompt.lastIndexOf(":") + 1).trim().toUpperCase();
            let cleanTicker = rawTicker.split(" ")[0].replace(/[^A-Z]/g, ""); 
            
            if (cleanTicker === "ABBINDIA") cleanTicker = "ABB";

            const isUSStock = ["AAPL", "NVDA", "MSFT", "TSLA", "ORCL", "LLY"].includes(cleanTicker);
            const yahooTicker = isUSStock ? cleanTicker : `${cleanTicker}.NS`;

            try {
                const yfResponse = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?interval=1d&range=5d`, {
                    headers: {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    }
                });
                
                const yfData = await yfResponse.json();
                
                if (yfData.chart && yfData.chart.result && yfData.chart.result[0]) {
                    const meta = yfData.chart.result[0].meta;
                    const indicators = yfData.chart.result[0].indicators.quote[0];
                    const livePrice = meta.regularMarketPrice;
                    const currency = meta.currency;
                    
                    const validCloses = indicators.close.filter(val => val != null);
                    const recentPricesText = validCloses.map(v => v.toFixed(2)).join(", ");

                    userPrompt = `The user wants an analytics report for ticker: ${cleanTicker}.
                    REAL-TIME MARKET METRICS OVERRIDE:
                    - Current Price: ${livePrice} ${currency}
                    - Recent Session Closings: ${recentPricesText}
                    
                    CRITICAL: Use ${livePrice} ${currency} as your exact base matrix. Calculate support bounds and moving averages tightly around this zone.`;
                }
            } catch (fetchErr) {
                console.log("Scraper loop bypass active.");
            }
        }

        const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${apiKey}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt + " Output raw statistics calculated using the data present in the prompt text. Discard internal training assumptions." },
                    { role: "user", content: userPrompt }
                ],
                temperature: 0.1
            })
        });

        const data = await response.json();
        if (data.choices && data.choices[0]) {
            return res.status(200).json({ content: data.choices[0].message.content });
        }
        return res.status(500).json({ error: 'Failing to parse tokens from engine.' });

    } catch (err) {
        return res.status(500).json({ error: 'Gateway loop routing fault.' });
    }
};